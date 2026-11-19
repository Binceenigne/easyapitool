package main

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	_ "modernc.org/sqlite"
)

var errInvalidKey = errors.New("API Key 无效、已停用或上游未启用")

type quotaError struct {
	resetAt  time.Time
	currency bool
}

func (e *quotaError) Error() string {
	if e.currency {
		return "USD 余额不足或已达分时额度"
	}
	return "请求次数已达用量上限"
}

type upstream struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	URL     string `json:"baseUrl"`
	Pool    string `json:"pool"`
	Secret  string `json:"-"`
	Enabled bool   `json:"enabled"`
}

type accessKey struct {
	ID       string    `json:"id"`
	Name     string    `json:"name"`
	Prefix   string    `json:"prefix"`
	Upstream string    `json:"upstreamId"`
	Limit5h  int64     `json:"limit5h"`
	Limit7d  int64     `json:"limit7d"`
	Policy   keyPolicy `json:"policy"`
	Enabled  bool      `json:"enabled"`
}

type meter struct {
	Input  int64
	Output int64
	Total  int64
	Detail object
}

type store struct {
	db   *sql.DB
	aead cipher.AEAD
}

func openStore(path, secret string) (*store, error) {
	key := sha256.Sum256([]byte("mirra:upstream:v1\x00" + secret))
	block, err := aes.NewCipher(key[:])
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	file.Close()
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	// A single writer keeps quota checks and reservations in transaction order.
	db.SetMaxOpenConns(1)
	_, err = db.Exec(`PRAGMA busy_timeout = 5000;
		PRAGMA foreign_keys = ON;
		PRAGMA journal_mode = WAL;
		CREATE TABLE IF NOT EXISTS upstreams (
		  id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, pool TEXT NOT NULL,
		  credential BLOB NOT NULL, enabled INTEGER NOT NULL
		);
		CREATE TABLE IF NOT EXISTS access_keys (
		  id TEXT PRIMARY KEY, name TEXT NOT NULL, prefix TEXT NOT NULL,
		  digest TEXT NOT NULL UNIQUE, upstream_id TEXT NOT NULL REFERENCES upstreams(id),
		  limit_5h INTEGER NOT NULL, limit_7d INTEGER NOT NULL, policy TEXT NOT NULL, enabled INTEGER NOT NULL
		);
		CREATE TABLE IF NOT EXISTS requests (
		  id INTEGER PRIMARY KEY, key_id TEXT NOT NULL REFERENCES access_keys(id),
		  created INTEGER NOT NULL, status INTEGER NOT NULL DEFAULT 0,
		  input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, usage_json TEXT,
		  reserve_micro INTEGER NOT NULL DEFAULT 0, cost_micro INTEGER
		);
		CREATE INDEX IF NOT EXISTS requests_key_time ON requests(key_id, created);
		CREATE TABLE IF NOT EXISTS affinity (
		  pool TEXT NOT NULL, hour INTEGER NOT NULL, key_id TEXT NOT NULL REFERENCES access_keys(id),
		  upstream_id TEXT NOT NULL REFERENCES upstreams(id), weight INTEGER NOT NULL,
		  PRIMARY KEY (pool,hour,key_id)
		);`)
	if err != nil {
		db.Close()
		return nil, err
	}
	return &store{db: db, aead: aead}, nil
}

func randomID(size int) string {
	value := make([]byte, size)
	if _, err := rand.Read(value); err != nil {
		panic(err)
	}
	return base64.RawURLEncoding.EncodeToString(value)
}

func keyDigest(secret string) string {
	sum := sha256.Sum256([]byte(secret))
	return hex.EncodeToString(sum[:])
}

func (s *store) saveUpstream(ctx context.Context, u upstream) error {
	if u.Secret == "" {
		res, err := s.db.ExecContext(ctx, `UPDATE upstreams SET name=?, pool=?, enabled=? WHERE id=? AND url=?`, u.Name, u.Pool, u.Enabled, u.ID, u.URL)
		if err != nil {
			return err
		}
		changed, err := res.RowsAffected()
		if err == nil && changed == 0 {
			return fmt.Errorf("添加上游或修改地址时需要填写 API Key")
		}
		return err
	}
	nonce := make([]byte, s.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return err
	}
	sealed := s.aead.Seal(nonce, nonce, []byte(u.Secret), []byte(u.ID))
	_, err := s.db.ExecContext(ctx, `INSERT INTO upstreams (id,name,url,pool,credential,enabled) VALUES (?,?,?,?,?,?)
		ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,pool=excluded.pool,credential=excluded.credential,enabled=excluded.enabled`,
		u.ID, u.Name, u.URL, u.Pool, sealed, u.Enabled)
	return err
}

func (s *store) listUpstreams(ctx context.Context) ([]upstream, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id,name,url,pool,enabled FROM upstreams ORDER BY name,id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []upstream{}
	for rows.Next() {
		var u upstream
		if err := rows.Scan(&u.ID, &u.Name, &u.URL, &u.Pool, &u.Enabled); err != nil {
			return nil, err
		}
		items = append(items, u)
	}
	return items, rows.Err()
}

func (s *store) getUpstream(ctx context.Context, id string) (upstream, error) {
	var u upstream
	var sealed []byte
	err := s.db.QueryRowContext(ctx, `SELECT id,name,url,pool,enabled,credential FROM upstreams WHERE id=?`, id).Scan(&u.ID, &u.Name, &u.URL, &u.Pool, &u.Enabled, &sealed)
	if err != nil {
		return u, err
	}
	nonceSize := s.aead.NonceSize()
	if len(sealed) < nonceSize {
		return u, fmt.Errorf("invalid upstream credential")
	}
	secret, err := s.aead.Open(nil, sealed[:nonceSize], sealed[nonceSize:], []byte(u.ID))
	u.Secret = string(secret)
	return u, err
}

func (s *store) issue(ctx context.Context, k accessKey, custom string) (accessKey, string, error) {
	if custom == "" {
		custom = randomID(32)
	}
	secret := "sk-mirra-" + custom
	k.ID, k.Prefix, k.Enabled = randomID(15), secret[:16], true
	_, err := s.db.ExecContext(ctx, `INSERT INTO access_keys (id,name,prefix,digest,upstream_id,limit_5h,limit_7d,policy,enabled) VALUES (?,?,?,?,?,?,?,?,1)`,
		k.ID, k.Name, k.Prefix, keyDigest(secret), k.Upstream, k.Limit5h, k.Limit7d, k.Policy.encode())
	if err != nil {
		return accessKey{}, "", err
	}
	return k, secret, nil
}

func (s *store) listKeys(ctx context.Context) ([]accessKey, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id,name,prefix,upstream_id,limit_5h,limit_7d,policy,enabled FROM access_keys ORDER BY name,id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []accessKey{}
	for rows.Next() {
		var k accessKey
		var policy string
		if err := rows.Scan(&k.ID, &k.Name, &k.Prefix, &k.Upstream, &k.Limit5h, &k.Limit7d, &policy, &k.Enabled); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(policy), &k.Policy); err != nil {
			return nil, err
		}
		items = append(items, k)
	}
	return items, rows.Err()
}

func (s *store) updateKey(ctx context.Context, k accessKey) error {
	res, err := s.db.ExecContext(ctx, `UPDATE access_keys SET name=?,upstream_id=?,limit_5h=?,limit_7d=?,policy=?,enabled=? WHERE id=?`,
		k.Name, k.Upstream, k.Limit5h, k.Limit7d, k.Policy.encode(), k.Enabled, k.ID)
	if err != nil {
		return err
	}
	changed, err := res.RowsAffected()
	if err == nil && changed == 0 {
		return sql.ErrNoRows
	}
	return err
}

func (s *store) authenticate(ctx context.Context, secret string) (accessKey, upstream, error) {
	var k accessKey
	var policy string
	err := s.db.QueryRowContext(ctx, `SELECT id,name,prefix,upstream_id,limit_5h,limit_7d,policy,enabled FROM access_keys WHERE digest=?`, keyDigest(secret)).
		Scan(&k.ID, &k.Name, &k.Prefix, &k.Upstream, &k.Limit5h, &k.Limit7d, &policy, &k.Enabled)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && !k.Enabled) {
		return k, upstream{}, errInvalidKey
	}
	if err != nil {
		return k, upstream{}, err
	}
	if err := json.Unmarshal([]byte(policy), &k.Policy); err != nil {
		return k, upstream{}, err
	}
	u, err := s.getUpstream(ctx, k.Upstream)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && !u.Enabled && u.Pool == "") {
		return k, u, errInvalidKey
	}
	return k, u, err
}

type usageWindow struct {
	Limit       int64 `json:"limit"`
	Used        int64 `json:"used"`
	Remaining   any   `json:"remaining"`
	ResetAt     any   `json:"reset_at"`
	WindowStart any   `json:"window_start"`
}

type queryRow interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

func readWindow(ctx context.Context, q queryRow, keyID string, limit int64, duration time.Duration, now time.Time) (usageWindow, error) {
	window := usageWindow{Limit: limit}
	var oldest sql.NullInt64
	err := q.QueryRowContext(ctx, `SELECT COUNT(*), MIN(created) FROM requests WHERE key_id=? AND created>?`,
		keyID, now.Add(-duration).UnixMilli()).Scan(&window.Used, &oldest)
	if limit > 0 {
		window.Remaining = max(0, limit-window.Used)
	}
	if oldest.Valid {
		start := time.UnixMilli(oldest.Int64).UTC()
		window.WindowStart = start
		window.ResetAt = start.Add(duration)
	}
	return window, err
}

func (s *store) reserve(ctx context.Context, selected route, now time.Time, estimate int64) (int64, error) {
	k := selected.key
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	var currentUpstream, anchorPool, anchorURL, pool, upstreamURL, policy string
	var enabled bool
	err = tx.QueryRowContext(ctx, `SELECT a.upstream_id,a.enabled AND u.enabled,a.limit_5h,a.limit_7d,a.policy,anchor.pool,anchor.url,u.pool,u.url
		FROM access_keys a JOIN upstreams anchor ON anchor.id=a.upstream_id JOIN upstreams u ON u.id=? WHERE a.id=?`, selected.upstream.ID, k.ID).
		Scan(&currentUpstream, &enabled, &k.Limit5h, &k.Limit7d, &policy, &anchorPool, &anchorURL, &pool, &upstreamURL)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && (!enabled || currentUpstream != k.Upstream || upstreamURL != selected.upstream.URL ||
		(currentUpstream != selected.upstream.ID && (pool == "" || pool != anchorPool || upstreamURL != anchorURL)))) {
		return 0, errInvalidKey
	}
	if err != nil {
		return 0, err
	}
	if err := json.Unmarshal([]byte(policy), &k.Policy); err != nil {
		return 0, err
	}
	if err := k.Policy.check(selected.clientIP, now); err != nil {
		return 0, err
	}
	var retryAt time.Time
	for _, rule := range []struct {
		limit int64
		span  time.Duration
	}{{k.Limit5h, 5 * time.Hour}, {k.Limit7d, 7 * 24 * time.Hour}} {
		if rule.limit == 0 {
			continue
		}
		var eligible int64
		err := tx.QueryRowContext(ctx, `SELECT created FROM requests WHERE key_id=? AND created>? ORDER BY created DESC,id DESC LIMIT 1 OFFSET ?`,
			k.ID, now.Add(-rule.span).UnixMilli(), rule.limit-1).Scan(&eligible)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return 0, err
		}
		if reset := time.UnixMilli(eligible).Add(rule.span); reset.After(retryAt) {
			retryAt = reset
		}
	}
	if !retryAt.IsZero() {
		return 0, &quotaError{resetAt: retryAt}
	}
	held := int64(0)
	if k.Policy.usdLimited() {
		if estimate <= 0 {
			return 0, errPricingUnavailable
		}
		held = estimate
		if err := checkBudget(ctx, tx, k, now, held); err != nil {
			return 0, err
		}
	}
	inserted, err := tx.ExecContext(ctx, `INSERT INTO requests(key_id,created,reserve_micro) VALUES (?,?,?)`, k.ID, now.UnixMilli(), held)
	if err != nil {
		return 0, err
	}
	id, err := inserted.LastInsertId()
	if err != nil {
		return 0, err
	}
	return id, tx.Commit()
}

func (s *store) finish(ctx context.Context, requestID int64, status int, count *meter, cost *int64) error {
	if requestID == 0 {
		return nil
	}
	if count == nil {
		_, err := s.db.ExecContext(ctx, `UPDATE requests SET status=?,cost_micro=? WHERE id=?`, status, cost, requestID)
		return err
	}
	detail, err := json.Marshal(count.Detail)
	if err != nil {
		return err
	}
	_, err = s.db.ExecContext(ctx, `UPDATE requests SET status=?,input_tokens=?,output_tokens=?,total_tokens=?,usage_json=?,cost_micro=? WHERE id=?`,
		status, count.Input, count.Output, count.Total, string(detail), cost, requestID)
	return err
}

func (s *store) usage(ctx context.Context, k accessKey, now time.Time) (object, error) {
	tx, err := s.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	windows := object{}
	rateLimits := []object{}
	for _, entry := range []struct {
		name string
		time time.Duration
		cap  int64
	}{{"5h", 5 * time.Hour, k.Limit5h}, {"1d", 24 * time.Hour, 0}, {"7d", 7 * 24 * time.Hour, k.Limit7d}} {
		window, err := readWindow(ctx, tx, k.ID, entry.cap, entry.time, now)
		if err != nil {
			return nil, err
		}
		windows[entry.name] = window
		rateLimits = append(rateLimits, object{"window": entry.name, "limit": window.Limit, "used": window.Used,
			"remaining": window.Remaining, "reset_at": window.ResetAt, "window_start": window.WindowStart})
	}
	periods := object{}
	for name, start := range map[string]int64{"today": now.UTC().Truncate(24 * time.Hour).UnixMilli(), "total": 0} {
		var requests, metered, input, output, total, priced, settled, held int64
		err := tx.QueryRowContext(ctx, `SELECT COUNT(*),COUNT(total_tokens),COALESCE(SUM(input_tokens),0),
			COALESCE(SUM(output_tokens),0),COALESCE(SUM(total_tokens),0),COUNT(cost_micro),COALESCE(SUM(cost_micro),0),
			COALESCE(SUM(CASE WHEN cost_micro IS NULL THEN reserve_micro ELSE 0 END),0) FROM requests WHERE key_id=? AND created>=?`,
			k.ID, start).Scan(&requests, &metered, &input, &output, &total, &priced, &settled, &held)
		if err != nil {
			return nil, err
		}
		periods[name] = object{"requests": requests, "meteredRequests": metered, "input_tokens": input, "output_tokens": output, "total_tokens": total,
			"pricedRequests": priced, "settledMicroUSD": settled, "reservedMicroUSD": held}
		if priced > 0 {
			asObject(periods[name])["cost"] = float64(settled) / 1_000_000
		}
	}
	payload := object{
		"isValid": k.Enabled, "status": "active", "mode": "request_quota", "planName": k.Name,
		"quotaUnit": "requests", "usage": periods, "rate_limits": rateLimits, "windows": windows, "expires_at": k.Policy.ExpiresAt,
	}
	if k.Policy.usdLimited() {
		payload["mode"], payload["quotaUnit"] = "usd_quota", "USD"
		budget := object{}
		for _, rule := range k.Policy.budgets() {
			item, err := budgetState(ctx, tx, k.ID, rule, now)
			if err != nil {
				return nil, err
			}
			budget[rule.name] = item
		}
		payload["usdBudget"] = budget
		payload["quota"] = budget["total"]
		if k.Policy.H5 > 0 || k.Policy.D1 > 0 || k.Policy.D7 > 0 {
			payload["rate_limits"] = []object{asObject(budget["5h"]), asObject(budget["1d"]), asObject(budget["7d"])}
		}
	}
	return payload, tx.Commit()
}
