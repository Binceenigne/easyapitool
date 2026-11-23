package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"
)

type perMinuteLimit int

func (limit *perMinuteLimit) UnmarshalJSON(data []byte) error {
	if bytes.Equal(bytes.TrimSpace(data), []byte("null")) {
		return errors.New("RPM 须为整数，0 表示不限")
	}
	var value int
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	*limit = perMinuteLimit(value)
	return nil
}

type rpmState struct {
	limit, used int64
	resetAt     time.Time
}

func (state rpmState) view() object {
	value := object{"limit": state.limit, "used": state.used, "remaining": nil,
		"resetAt": nil, "windowSeconds": 60, "shared": true}
	if state.limit > 0 {
		value["remaining"] = max(int64(0), state.limit-state.used)
		if !state.resetAt.IsZero() {
			value["resetAt"] = state.resetAt.UTC()
		}
	}
	return value
}

type rpmError struct{ state rpmState }

func (err *rpmError) Error() string {
	return fmt.Sprintf("请求过于频繁：此密钥每 60 秒最多调用模型 %d 次，语言和图片共享此限制，请稍后重试", err.state.limit)
}

func (err *rpmError) retryAfter() int {
	return max(1, int(math.Ceil(time.Until(err.state.resetAt).Seconds())))
}

func rpmFailure(err error) object {
	var limited *rpmError
	if !errors.As(err, &limited) {
		return nil
	}
	return object{"status": 429, "errorCode": "rpm_limit_exceeded", "retryAfter": limited.retryAfter(), "rateLimit": limited.state.view()}
}

func attachRPMFailure(target object, err error) {
	for name, value := range rpmFailure(err) {
		target[name] = value
	}
}

// Read the tier in the same database snapshot as the key and admission ledger.
// Model edits and tier migration therefore take effect at their commit boundary.
func tierForKeyTx(ctx context.Context, tx *sql.Tx, key accessKey) (featureTier, error) {
	var data []byte
	err := tx.QueryRowContext(ctx, "SELECT value FROM gateway_settings WHERE name='models'").Scan(&data)
	var settings modelSettings
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return featureTier{}, err
	}
	if err == nil {
		if err := json.Unmarshal(data, &settings); err != nil {
			return featureTier{}, err
		}
	}
	settings.ensureTiers()
	tier, ok := settings.tier(settings.keyTierID(key))
	if !ok {
		return featureTier{}, denied("此档位")
	}
	if tier.RPMLimit < 0 || tier.RPMLimit > 1_000_000 {
		return featureTier{}, errors.New("已保存的 RPM 设置无效")
	}
	return tier, nil
}

func rpmStateTx(ctx context.Context, tx *sql.Tx, key accessKey, now time.Time) (rpmState, error) {
	tier, err := tierForKeyTx(ctx, tx, key)
	if err != nil {
		return rpmState{}, err
	}
	state := rpmState{limit: int64(tier.RPMLimit)}
	var oldest sql.NullInt64
	start := now.Add(-time.Minute).UnixMilli()
	// Callers capture now before waiting for SQLite's writer connection. Include
	// a later timestamp already committed by another caller, or arrival inversion
	// could admit two concurrent requests into a one-request window.
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*),MIN(created) FROM requests
		WHERE key_id=? AND created>?`, key.ID, start).Scan(&state.used, &oldest); err != nil {
		return rpmState{}, err
	}
	if state.limit > 0 && state.used >= state.limit {
		// Lowering a tier limit can leave more than limit entries in the window.
		// Wait until enough entries expire, rather than promising the first expiry.
		if err := tx.QueryRowContext(ctx, `SELECT created FROM requests WHERE key_id=? AND created>?
			ORDER BY created DESC,id DESC LIMIT 1 OFFSET ?`, key.ID, start, state.limit-1).Scan(&oldest); err != nil {
			return rpmState{}, err
		}
	}
	if oldest.Valid {
		state.resetAt = time.UnixMilli(oldest.Int64).Add(time.Minute)
	}
	return state, nil
}

func checkRPMTx(ctx context.Context, tx *sql.Tx, key accessKey, now time.Time) error {
	state, err := rpmStateTx(ctx, tx, key, now)
	if err != nil {
		return err
	}
	if state.limit > 0 && state.used >= state.limit {
		return &rpmError{state}
	}
	return nil
}

func (g *gateway) checkRPM(ctx context.Context, selected route) error {
	if selected.key.ID == "" {
		return nil
	}
	tx, err := g.store.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	key, err := readKey(ctx, tx, selected.key.ID)
	if err == nil {
		err = checkRPMTx(ctx, tx, key, time.Now())
	}
	return err
}
