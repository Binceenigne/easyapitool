package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"slices"
	"strconv"
	"time"
)

// Match the desktop client's business timezone and the production Sub2 database calendar.
var billingZone = time.FixedZone("Asia/Shanghai", 8*60*60)
var errQuotaConflict = errors.New("额度已变化，请刷新后重试")

func midnight(t time.Time) time.Time {
	local := t.In(billingZone)
	return time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, billingZone)
}

type quotaPeriod struct{ start, afterID int64 }

func periodStart(rule budgetRule, t time.Time) int64 {
	if rule.name == "5h" {
		return t.UnixMilli()
	}
	return midnight(t).UnixMilli()
}

func putPeriod(ctx context.Context, tx *sql.Tx, id, window string, p quotaPeriod) error {
	_, err := tx.ExecContext(ctx, `INSERT INTO quota_periods(key_id,window,start_ms,after_request) VALUES(?,?,?,?)
		ON CONFLICT(key_id,window) DO UPDATE SET start_ms=excluded.start_ms,after_request=excluded.after_request`, id, window, p.start, p.afterID)
	return err
}

func quotaEvent(ctx context.Context, tx *sql.Tx, id, kind, window string, previous, current int64, now time.Time) error {
	_, err := tx.ExecContext(ctx, `INSERT INTO quota_events(key_id,created,kind,window,before_micro,after_micro) VALUES(?,?,?,?,?,?)`,
		id, now.UnixMilli(), kind, window, previous, current)
	return err
}

func loadPeriods(ctx context.Context, tx *sql.Tx, k accessKey, now time.Time, dispatch bool) (map[string]quotaPeriod, error) {
	rows, err := tx.QueryContext(ctx, "SELECT window,start_ms,after_request FROM quota_periods WHERE key_id=?", k.ID)
	if err != nil {
		return nil, err
	}
	periods := map[string]quotaPeriod{}
	for rows.Next() {
		var win string
		var p quotaPeriod
		if err := rows.Scan(&win, &p.start, &p.afterID); err != nil {
			rows.Close()
			return nil, err
		}
		periods[win] = p
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, err
	}
	rows.Close()
	rules := k.Policy.budgets()
	if len(periods) == 0 {
		// Reconstruct window anchors once for keys with requests from an earlier release.
		rows, err := tx.QueryContext(ctx, "SELECT created FROM requests WHERE key_id=? ORDER BY created,id", k.ID)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var at int64
			if err := rows.Scan(&at); err != nil {
				rows.Close()
				return nil, err
			}
			for _, rule := range rules {
				if rule.span == 0 {
					continue
				}
				p := periods[rule.name]
				if p.start == 0 || at >= p.start+rule.span.Milliseconds() {
					periods[rule.name] = quotaPeriod{start: periodStart(rule, time.UnixMilli(at))}
				}
			}
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return nil, err
		}
		rows.Close()
		for _, rule := range rules {
			p := periods[rule.name]
			periods[rule.name] = p
			if err := putPeriod(ctx, tx, k.ID, rule.name, p); err != nil {
				return nil, err
			}
		}
	}
	for _, rule := range rules {
		if rule.span == 0 {
			continue
		}
		p := periods[rule.name]
		expired := p.start != 0 && now.UnixMilli() >= p.start+rule.span.Milliseconds()
		if !expired && !(dispatch && p.start == 0) {
			continue
		}
		if expired {
			var used int64
			err := tx.QueryRowContext(ctx, `SELECT COALESCE(SUM(COALESCE(cost_micro,reserve_micro)),0) FROM requests
				WHERE key_id=? AND created>=? AND created<? AND id>?`, k.ID, p.start, p.start+rule.span.Milliseconds(), p.afterID).Scan(&used)
			if err != nil {
				return nil, err
			}
			if err := quotaEvent(ctx, tx, k.ID, "window_refreshed", rule.name, used, 0, now); err != nil {
				return nil, err
			}
		}
		p.start = periodStart(rule, now)
		if err := putPeriod(ctx, tx, k.ID, rule.name, p); err != nil {
			return nil, err
		}
		periods[rule.name] = p
	}
	return periods, nil
}

func readKey(ctx context.Context, tx *sql.Tx, id string) (accessKey, error) {
	var k accessKey
	var policy string
	err := tx.QueryRowContext(ctx, `SELECT id,name,prefix,upstream_id,policy,enabled FROM access_keys WHERE id=?
		AND NOT EXISTS(SELECT 1 FROM deleted_keys WHERE key_id=access_keys.id)`, id).
		Scan(&k.ID, &k.Name, &k.Prefix, &k.Upstream, &policy, &k.Enabled)
	if err == nil {
		err = json.Unmarshal([]byte(policy), &k.Policy)
	}
	return k, err
}

type quotaEventView struct {
	ID        int64   `json:"id"`
	Kind      string  `json:"kind"`
	Window    string  `json:"window"`
	Previous  float64 `json:"previous"`
	Current   float64 `json:"current"`
	ChangedAt float64 `json:"changedAt"`
	Message   string  `json:"message"`
}

func windowLabel(name string) string {
	switch name {
	case "5h":
		return "5 小时"
	case "1d":
		return "24 小时"
	case "7d":
		return "7 天"
	default:
		return "总额度"
	}
}
func amountLabel(value float64) string {
	if value == 0 {
		return "不限"
	}
	return fmt.Sprintf("%g USD", value)
}

func readQuotaEvents(ctx context.Context, tx *sql.Tx, id string, after int64, latest bool) ([]quotaEventView, error) {
	query := `SELECT id,kind,window,before_micro,after_micro,created FROM quota_events WHERE key_id=? AND id>? ORDER BY id`
	if latest {
		query += " DESC"
	}
	rows, err := tx.QueryContext(ctx, query+" LIMIT 64", id, after)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []quotaEventView{}
	for rows.Next() {
		var item quotaEventView
		var before, current, created int64
		if err := rows.Scan(&item.ID, &item.Kind, &item.Window, &before, &current, &created); err != nil {
			return nil, err
		}
		item.Previous, item.Current, item.ChangedAt = float64(before)/1e6, float64(current)/1e6, float64(created)/1000
		switch item.Kind {
		case "limit_changed":
			item.Message = windowLabel(item.Window) + "上限由 " + amountLabel(item.Previous) + " 调整为 " + amountLabel(item.Current)
		case "usage_reset":
			item.Message = windowLabel(item.Window) + "已用额度已手动重置"
		case "window_refreshed":
			item.Message = windowLabel(item.Window) + "窗口已刷新"
		case "tier_changed":
			item.Message = "功能档位已更新"
		case "key_updated":
			item.Message = "密钥设置已更新"
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func attachQuotaFeedback(payload object, events []quotaEventView, now time.Time) {
	var revision int64
	changes := object{}
	for _, e := range events {
		revision = max(revision, e.ID)
		name := e.Window
		if name == "total" {
			name = "quota"
		}
		if _, exists := changes[name]; !exists && float64(now.UnixMilli())/1000-e.ChangedAt <= 600 {
			changes[name] = e
		}
	}
	payload["quotaRevision"], payload["quotaEvents"], payload["_limit_changes"] = revision, events, changes
	payload["serverQuota"] = true
	payload["asOf"], payload["timezone"] = now.UTC(), billingZone.String()
}

func (s *store) changeKey(ctx context.Context, id string, updated *accessKey, reset string, revision *int64, now time.Time) (object, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	k, err := readKey(ctx, tx, id)
	if err != nil {
		return nil, err
	}
	if revision != nil {
		var current int64
		if err := tx.QueryRowContext(ctx, "SELECT COALESCE(MAX(id),0) FROM quota_events WHERE key_id=?", id).Scan(&current); err != nil {
			return nil, err
		}
		if current != *revision {
			return nil, errQuotaConflict
		}
	}
	periods, err := loadPeriods(ctx, tx, k, now, false)
	if err != nil {
		return nil, err
	}
	if updated != nil {
		sameExpiry := k.Policy.ExpiresAt == nil && updated.Policy.ExpiresAt == nil ||
			k.Policy.ExpiresAt != nil && updated.Policy.ExpiresAt != nil && k.Policy.ExpiresAt.Equal(*updated.Policy.ExpiresAt)
		if k.Policy.TierID != updated.Policy.TierID {
			if err := quotaEvent(ctx, tx, id, "tier_changed", "permissions", 0, 0, now); err != nil {
				return nil, err
			}
		} else if k.Name != updated.Name || k.Upstream != updated.Upstream || k.Enabled != updated.Enabled ||
			!sameExpiry || !slices.Equal(k.Policy.IPs, updated.Policy.IPs) {
			if err := quotaEvent(ctx, tx, id, "key_updated", "settings", 0, 0, now); err != nil {
				return nil, err
			}
		}
		oldRules := k.Policy.budgets()
		for i, rule := range updated.Policy.budgets() {
			if oldRules[i].limit != rule.limit {
				if err := quotaEvent(ctx, tx, id, "limit_changed", rule.name, oldRules[i].limit, rule.limit, now); err != nil {
					return nil, err
				}
			}
		}
		_, err = tx.ExecContext(ctx, `UPDATE access_keys SET name=?,upstream_id=?,limit_5h=0,limit_7d=0,policy=?,enabled=? WHERE id=?`,
			updated.Name, updated.Upstream, updated.Policy.encode(), updated.Enabled, id)
		if err != nil {
			return nil, err
		}
		k = *updated
	}
	if reset != "" {
		var lastID int64
		if err := tx.QueryRowContext(ctx, "SELECT COALESCE(MAX(id),0) FROM requests WHERE key_id=?", id).Scan(&lastID); err != nil {
			return nil, err
		}
		for _, rule := range k.Policy.budgets() {
			if reset != rule.name && reset != "all" && !(reset == "windows" && rule.span != 0) {
				continue
			}
			before, err := periodUsage(ctx, tx, id, periods[rule.name])
			if err != nil {
				return nil, err
			}
			if err := putPeriod(ctx, tx, id, rule.name, quotaPeriod{afterID: lastID}); err != nil {
				return nil, err
			}
			if err := quotaEvent(ctx, tx, id, "usage_reset", rule.name, before, 0, now); err != nil {
				return nil, err
			}
		}
	}
	data, err := s.usageTx(ctx, tx, k, now)
	if err != nil {
		return nil, err
	}
	return data, tx.Commit()
}

func (a *admin) resetQuota(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Scope    string
		Revision *int64
	}
	if !decodeRequest(w, r, 4096, &input) {
		return
	}
	if !oneOf(input.Scope, "total", "windows", "all", "5h", "1d", "7d") || input.Revision == nil {
		jsonError(w, 400, "请选择要重置的额度并刷新密钥状态")
		return
	}
	usage, err := a.gateway.store.changeKey(r.Context(), r.PathValue("id"), nil, input.Scope, input.Revision, time.Now())
	if err != nil {
		quotaMutationError(w, err)
		return
	}
	a.gateway.notifyQuota(r.PathValue("id"), usage)
	writeJSON(w, 200, object{"ok": true, "action": "usage_reset", "usage": usage})
}

func quotaMutationError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, errQuotaConflict):
		jsonError(w, 409, err.Error())
	case errors.Is(err, sql.ErrNoRows):
		jsonError(w, 404, "密钥不存在")
	default:
		jsonError(w, 503, "额度更新失败，请刷新后重试")
	}
}

func (g *gateway) quotaEvents(w http.ResponseWriter, r *http.Request) {
	selected, err := g.resolveRoute(r.Context(), bearer(r), g.clientIP(r), false)
	if err != nil {
		g.proxyError(w, err)
		return
	}
	after := int64(0)
	if raw := r.URL.Query().Get("after"); raw != "" {
		var parseErr error
		after, parseErr = strconv.ParseInt(raw, 10, 64)
		if parseErr != nil || after < 0 {
			jsonError(w, 400, "事件游标无效")
			return
		}
	}
	tx, err := g.store.db.BeginTx(r.Context(), nil)
	if err != nil {
		g.proxyError(w, err)
		return
	}
	defer tx.Rollback()
	if _, err = loadPeriods(r.Context(), tx, selected.key, time.Now(), false); err != nil {
		g.proxyError(w, err)
		return
	}
	items, err := readQuotaEvents(r.Context(), tx, selected.key.ID, after, false)
	if err != nil {
		g.proxyError(w, err)
		return
	}
	if err := tx.Commit(); err != nil {
		g.proxyError(w, err)
		return
	}
	cursor := after
	if len(items) > 0 {
		cursor = items[len(items)-1].ID
	}
	writeJSON(w, 200, object{"events": items, "nextCursor": cursor, "unit": "USD"})
}
