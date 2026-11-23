package main

import (
	"context"
	"database/sql"
	"errors"
	"net/http"
	"time"
)

func (s *store) revealKey(ctx context.Context, id string) (string, error) {
	var sealed []byte
	err := s.db.QueryRowContext(ctx, `SELECT credential FROM key_secrets WHERE key_id=?
		AND NOT EXISTS(SELECT 1 FROM deleted_keys WHERE key_id=?)`, id, id).Scan(&sealed)
	if err != nil {
		return "", err
	}
	data, err := s.unseal(sealed, "distributed-key\x00"+id)
	return string(data), err
}

// Keep accounting receipts, but revoke the key and erase its recoverable secret.
func (s *store) deleteKey(ctx context.Context, id string, revision *int64) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := readKey(ctx, tx, id); err != nil {
		return err
	}
	if revision != nil {
		var current int64
		if err := tx.QueryRowContext(ctx, "SELECT COALESCE(MAX(id),0) FROM quota_events WHERE key_id=?", id).Scan(&current); err != nil {
			return err
		}
		if current != *revision {
			return errQuotaConflict
		}
	}
	if _, err = tx.ExecContext(ctx, "UPDATE access_keys SET enabled=0 WHERE id=?", id); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO deleted_keys(key_id,deleted_at) VALUES(?,?)", id, time.Now().UnixMilli()); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, "DELETE FROM key_secrets WHERE key_id=?", id); err != nil {
		return err
	}
	return tx.Commit()
}

func (a *admin) revealKey(w http.ResponseWriter, r *http.Request) {
	secret, err := a.gateway.store.revealKey(r.Context(), r.PathValue("id"))
	if errors.Is(err, sql.ErrNoRows) {
		jsonError(w, 404, "密钥不存在或没有可查看的完整值")
		return
	}
	if err != nil {
		jsonError(w, 503, "暂时无法读取密钥")
		return
	}
	writeJSON(w, 200, object{"secret": secret})
}

func (a *admin) deleteKey(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Revision *int64 `json:"revision"`
	}
	if !decodeRequest(w, r, 4096, &input) {
		return
	}
	a.gateway.modelMu.Lock()
	err := a.gateway.store.deleteKey(r.Context(), r.PathValue("id"), input.Revision)
	a.gateway.modelMu.Unlock()
	if err != nil {
		quotaMutationError(w, err)
		return
	}
	a.gateway.notifyQuota(r.PathValue("id"), object{"action": "key_deleted"})
	writeJSON(w, 200, object{"ok": true, "action": "key_deleted"})
}
