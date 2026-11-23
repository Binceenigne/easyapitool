package main

import (
	"context"
	"database/sql"
	"errors"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"time"
)

func (g *gateway) clientIP(r *http.Request) netip.Addr {
	remote, _, _ := net.SplitHostPort(r.RemoteAddr)
	ip, _ := netip.ParseAddr(remote)
	if g.cfg.trustLocalProxy && ip.IsLoopback() {
		if forwarded, err := netip.ParseAddr(r.Header.Get("X-Real-IP")); err == nil {
			return forwarded.Unmap()
		}
	}
	return ip.Unmap()
}

func (g *gateway) resolveRoute(ctx context.Context, secret string, ip netip.Addr, dispatch bool) (route, error) {
	k, anchor, err := g.store.authenticate(ctx, secret)
	if err != nil {
		return route{}, err
	}
	return g.routeForKey(ctx, k, anchor, ip, dispatch)
}

func (g *gateway) routeForKey(ctx context.Context, k accessKey, anchor upstream, ip netip.Addr, dispatch bool) (route, error) {
	now := time.Now()
	if err := k.Policy.check(ip, now); err != nil {
		return route{}, err
	}
	u := anchor
	var err error
	if anchor.Pool != "" {
		if dispatch {
			u, err = g.store.chooseUpstream(ctx, k, anchor, now)
		} else if !anchor.Enabled {
			err = g.store.db.QueryRowContext(ctx, `SELECT id FROM upstreams WHERE pool=? AND url=? AND enabled=1 LIMIT 1`, anchor.Pool, anchor.URL).Scan(&u.ID)
		}
		if errors.Is(err, sql.ErrNoRows) {
			return route{}, errInvalidKey
		}
		if err != nil {
			return route{}, err
		}
	}
	return route{key: k, upstream: u, clientIP: ip}, nil
}

func (s *store) chooseUpstream(ctx context.Context, k accessKey, anchor upstream, now time.Time) (upstream, error) {
	const hour = time.Hour
	window := now.Truncate(hour).UnixMilli()
	previous := window - hour.Milliseconds()
	// Separate pools that happen to share a name across different API origins.
	pool := anchor.Pool + "\x00" + anchor.URL
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return upstream{}, err
	}
	defer tx.Rollback()
	var selected string
	err = tx.QueryRowContext(ctx, `SELECT a.upstream_id FROM affinity a JOIN upstreams u ON u.id=a.upstream_id
		WHERE a.pool=? AND a.hour=? AND a.key_id=? AND u.enabled=1 AND u.pool=? AND u.url=?`, pool, window, k.ID, anchor.Pool, anchor.URL).Scan(&selected)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return upstream{}, err
	}
	if selected == "" {
		var preferred string
		err := tx.QueryRowContext(ctx, `SELECT upstream_id FROM affinity WHERE pool=? AND key_id=? AND hour<? ORDER BY hour DESC LIMIT 1`, pool, k.ID, window).Scan(&preferred)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return upstream{}, err
		}
		// Use last hour's observed load to break projected-load ties before falling back to affinity.
		err = tx.QueryRowContext(ctx, `WITH previous_load AS (
			SELECT a.upstream_id, COUNT(*) AS requests FROM affinity a JOIN requests r ON r.key_id=a.key_id
			AND r.created>=? AND r.created<? WHERE a.pool=? AND a.hour=? GROUP BY a.upstream_id
		) SELECT u.id FROM upstreams u LEFT JOIN affinity a
			ON a.upstream_id=u.id AND a.pool=? AND a.hour=? AND a.key_id<>?
			LEFT JOIN previous_load p ON p.upstream_id=u.id
			WHERE u.pool=? AND u.url=? AND u.enabled=1
			GROUP BY u.id ORDER BY COALESCE(SUM(a.weight),0), COALESCE(p.requests,0), u.id<>?,u.id LIMIT 1`,
			previous, window, pool, previous, pool, window, k.ID, anchor.Pool, anchor.URL, preferred).Scan(&selected)
		if err != nil {
			return upstream{}, err
		}
		var weight int64
		if err := tx.QueryRowContext(ctx, `SELECT MAX(1,COUNT(*)) FROM requests WHERE key_id=? AND created>=? AND created<?`, k.ID, previous, window).Scan(&weight); err != nil {
			return upstream{}, err
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO affinity (pool,hour,key_id,upstream_id,weight) VALUES (?,?,?,?,?)
			ON CONFLICT(pool,hour,key_id) DO UPDATE SET upstream_id=excluded.upstream_id,weight=excluded.weight`, pool, window, k.ID, selected, weight); err != nil {
			return upstream{}, err
		}
		if _, err := tx.ExecContext(ctx, `DELETE FROM affinity WHERE hour<?`, window-48*hour.Milliseconds()); err != nil {
			return upstream{}, err
		}
	}
	if err := tx.Commit(); err != nil {
		return upstream{}, err
	}
	return s.getUpstream(ctx, selected)
}

func (g *gateway) reserve(ctx context.Context, selected route) (int64, error) {
	if selected.key.ID == "" {
		return 0, nil
	}
	estimate := g.cfg.reserveMicroUSD
	if len(g.cfg.pricing) == 0 {
		estimate = 0
	}
	return g.store.reserveWithClock(ctx, selected, time.Now, estimate)
}

func validPool(name string) bool {
	return name == "" || (len(name) <= 64 && modelPattern.MatchString(name) && !strings.Contains(name, ":"))
}
