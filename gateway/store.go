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
	resetAt              time.Time
	window               string
	limit, used, reserve int64
}

func (e *quotaError) Error() string {
	return windowLabel(e.window) + "可用 USD 额度不足"
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
	Policy   keyPolicy `json:"policy"`
	Enabled  bool      `json:"enabled"`
}

type meter struct {
	Input   int64
	Output  int64
	Total   int64
	Detail  object
	notSent bool
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
		);
		CREATE TABLE IF NOT EXISTS gateway_settings (name TEXT PRIMARY KEY, value BLOB NOT NULL);
		CREATE TABLE IF NOT EXISTS quota_periods (
		  key_id TEXT NOT NULL REFERENCES access_keys(id), window TEXT NOT NULL, start_ms INTEGER NOT NULL,
		  after_request INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(key_id,window)
		);
		CREATE TABLE IF NOT EXISTS quota_events (
		  id INTEGER PRIMARY KEY, key_id TEXT NOT NULL REFERENCES access_keys(id), created INTEGER NOT NULL,
		  kind TEXT NOT NULL, window TEXT NOT NULL, before_micro INTEGER NOT NULL, after_micro INTEGER NOT NULL
		);
		CREATE INDEX IF NOT EXISTS quota_events_by_key ON quota_events(key_id,id);`)
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
	return s.writeUpstream(ctx, s.db, u)
}

type contextExecer interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

func (s *store) seal(data []byte, purpose string) []byte {
	nonce := make([]byte, s.aead.NonceSize())
	rand.Read(nonce)
	return s.aead.Seal(nonce, nonce, data, []byte(purpose))
}

func (s *store) unseal(sealed []byte, purpose string) ([]byte, error) {
	n := s.aead.NonceSize()
	if len(sealed) < n {
		return nil, fmt.Errorf("invalid encrypted value")
	}
	return s.aead.Open(nil, sealed[:n], sealed[n:], []byte(purpose))
}

func (s *store) writeUpstream(ctx context.Context, db contextExecer, u upstream) error {
	if u.Secret == "" {
		res, err := db.ExecContext(ctx, `UPDATE upstreams SET name=?, pool=?, enabled=? WHERE id=? AND url=?`, u.Name, u.Pool, u.Enabled, u.ID, u.URL)
		if err != nil {
			return err
		}
		changed, err := res.RowsAffected()
		if err == nil && changed == 0 {
			return fmt.Errorf("添加上游或修改地址时需要填写 API Key")
		}
		return err
	}
	sealed := s.seal([]byte(u.Secret), u.ID)
	_, err := db.ExecContext(ctx, `INSERT INTO upstreams (id,name,url,pool,credential,enabled) VALUES (?,?,?,?,?,?)
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
	secret, err := s.unseal(sealed, u.ID)
	u.Secret = string(secret)
	return u, err
}

func (s *store) issue(ctx context.Context, k accessKey, custom string) (accessKey, string, object, error) {
	if custom == "" {
		custom = randomID(32)
	}
	secret := "sk-mirra-" + custom
	k.ID, k.Prefix = randomID(15), secret[:16]
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return accessKey{}, "", nil, err
	}
	defer tx.Rollback()
	_, err = tx.ExecContext(ctx, `INSERT INTO access_keys (id,name,prefix,digest,upstream_id,limit_5h,limit_7d,policy,enabled) VALUES (?,?,?,?,?,0,0,?,?)`,
		k.ID, k.Name, k.Prefix, keyDigest(secret), k.Upstream, k.Policy.encode(), k.Enabled)
	if err != nil {
		return accessKey{}, "", nil, err
	}
	snapshot, err := s.usageTx(ctx, tx, k, time.Now())
	if err != nil {
		return accessKey{}, "", nil, err
	}
	if err = tx.Commit(); err != nil {
		return accessKey{}, "", nil, err
	}
	return k, secret, snapshot, nil
}

func (s *store) listKeys(ctx context.Context) ([]accessKey, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id,name,prefix,upstream_id,policy,enabled FROM access_keys ORDER BY name,id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []accessKey{}
	for rows.Next() {
		var k accessKey
		var policy string
		if err := rows.Scan(&k.ID, &k.Name, &k.Prefix, &k.Upstream, &policy, &k.Enabled); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(policy), &k.Policy); err != nil {
			return nil, err
		}
		items = append(items, k)
	}
	return items, rows.Err()
}

func (s *store) authenticate(ctx context.Context, secret string) (accessKey, upstream, error) {
	var k accessKey
	var policy string
	err := s.db.QueryRowContext(ctx, `SELECT id,name,prefix,upstream_id,policy,enabled FROM access_keys WHERE digest=?`, keyDigest(secret)).
		Scan(&k.ID, &k.Name, &k.Prefix, &k.Upstream, &policy, &k.Enabled)
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

func (s *store) reserve(ctx context.Context, selected route, now time.Time, estimate int64) (int64, error) {
	k := selected.key
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	var currentUpstream, anchorPool, anchorURL, pool, upstreamURL, policy string
	var enabled bool
	err = tx.QueryRowContext(ctx, `SELECT a.upstream_id,a.enabled AND u.enabled,a.policy,anchor.pool,anchor.url,u.pool,u.url
		FROM access_keys a JOIN upstreams anchor ON anchor.id=a.upstream_id JOIN upstreams u ON u.id=? WHERE a.id=?`, selected.upstream.ID, k.ID).
		Scan(&currentUpstream, &enabled, &policy, &anchorPool, &anchorURL, &pool, &upstreamURL)
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
	periods, err := loadPeriods(ctx, tx, k, now, true)
	if err != nil {
		return 0, err
	}
	held := int64(0)
	if k.Policy.usdLimited() {
		if estimate <= 0 {
			return 0, errPricingUnavailable
		}
		held = estimate
		if err := checkBudget(ctx, tx, k, periods, held); err != nil {
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
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	k, err = readKey(ctx, tx, k.ID)
	if err != nil {
		return nil, err
	}
	data, err := s.usageTx(ctx, tx, k, now)
	if err != nil {
		return nil, err
	}
	return data, tx.Commit()
}

func (s *store) usageTx(ctx context.Context, tx *sql.Tx, k accessKey, now time.Time) (object, error) {
	cycles, err := loadPeriods(ctx, tx, k, now, false)
	if err != nil {
		return nil, err
	}
	windows := object{}
	budget := object{}
	requestsByWindow := object{}
	var rateLimits []object
	for _, rule := range k.Policy.budgets() {
		item, err := budgetState(ctx, tx, k.ID, rule, cycles[rule.name])
		if err != nil {
			return nil, err
		}
		budget[rule.name] = item
		if rule.span != 0 {
			var count int64
			if err := tx.QueryRowContext(ctx, "SELECT COUNT(*) FROM requests WHERE key_id=? AND created>?", k.ID, now.Add(-rule.span).UnixMilli()).Scan(&count); err != nil {
				return nil, err
			}
			requestsByWindow[rule.name] = count
			windows[rule.name] = item
			rateLimits = append(rateLimits, item)
		}
	}
	periods := object{}
	for name, start := range map[string]int64{"today": midnight(now).UnixMilli(), "total": 0} {
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
		"isValid": k.Enabled, "status": "active", "mode": "usd_quota", "planName": k.Name,
		"quotaUnit": "USD", "usage": periods, "rate_limits": rateLimits, "windows": windows,
		"expires_at": k.Policy.ExpiresAt, "quota": budget["total"], "usdBudget": budget, "requestCounts": requestsByWindow,
	}
	events, err := readQuotaEvents(ctx, tx, k.ID, 0, true)
	if err != nil {
		return nil, err
	}
	attachQuotaFeedback(payload, events, now)
	stats, err := usageRates(ctx, tx, k.ID, now)
	if err != nil {
		return nil, err
	}
	payload["rates"] = stats
	if k.Policy.ExpiresAt != nil {
		payload["expires_at"] = k.Policy.ExpiresAt.UTC().Format(time.RFC3339Nano)
	}
	return payload, nil
}
