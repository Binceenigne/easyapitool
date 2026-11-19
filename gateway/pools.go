package main

import (
	"database/sql"
	"net/http"
	"strings"
)

type poolInput struct {
	Name    string `json:"name"`
	BaseURL string `json:"baseUrl"`
	Members []struct {
		Name   string `json:"name"`
		APIKey string `json:"apiKey"`
	} `json:"members"`
}

func (a *admin) createPool(w http.ResponseWriter, r *http.Request) {
	var p poolInput
	if !decodeRequest(w, r, 384<<10, &p) {
		return
	}
	p.Name = strings.TrimSpace(p.Name)
	base, err := a.gateway.cfg.baseURL(p.BaseURL)
	if err != nil || len(p.BaseURL) > 2048 || p.Name == "" || !validPool(p.Name) || len(p.Members) < 1 || len(p.Members) > 64 {
		jsonError(w, http.StatusBadRequest, "请填写有效的号池名称、允许的 API 地址及 1 至 64 个成员")
		return
	}
	members := make([]upstream, 0, len(p.Members))
	seen := make(map[string]bool)
	for _, member := range p.Members {
		name, secret := strings.TrimSpace(member.Name), strings.TrimSpace(member.APIKey)
		if name == "" || len(name) > 120 || !validSecret(secret) || (strings.HasPrefix(secret, "sk-proj-") && base.String() != openAIBaseURL) {
			jsonError(w, http.StatusBadRequest, "成员名称、上游密钥或 API 地址无效")
			return
		}
		if seen[secret] {
			jsonError(w, http.StatusBadRequest, "同一号池表单中重复填写了 API Key")
			return
		}
		seen[secret] = true
		members = append(members, upstream{ID: randomID(15), Name: name, Pool: p.Name, URL: base.String(), Secret: secret, Enabled: true})
	}
	s := a.gateway.store
	ctx := r.Context()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		jsonError(w, http.StatusServiceUnavailable, "无法连接数据库")
		return
	}
	defer tx.Rollback()
	var exists bool
	if err = tx.QueryRowContext(ctx, "SELECT EXISTS(SELECT 1 FROM upstreams WHERE pool=? AND url=?)", p.Name, base.String()).Scan(&exists); err == nil && exists {
		jsonError(w, http.StatusConflict, "该地址下已有同名号池，可在该池中添加成员")
		return
	}
	for _, member := range members {
		if err != nil {
			break
		}
		err = s.writeUpstream(ctx, tx, member)
	}
	if err != nil || tx.Commit() != nil {
		jsonError(w, http.StatusServiceUnavailable, "号池未保存，请重试")
		return
	}
	writeJSON(w, http.StatusCreated, object{"members": members})
}

func (a *admin) renamePool(w http.ResponseWriter, r *http.Request) {
	var p struct{ ID, Name, Previous string }
	if !decodeRequest(w, r, 4096, &p) {
		return
	}
	p.Name = strings.TrimSpace(p.Name)
	if p.Name == "" || !validPool(p.Name) || p.Previous == "" || p.Name == p.Previous {
		jsonError(w, http.StatusBadRequest, "请填写新的有效号池名称")
		return
	}
	ctx := r.Context()
	tx, err := a.gateway.store.db.BeginTx(ctx, nil)
	if err != nil {
		jsonError(w, http.StatusServiceUnavailable, "无法连接数据库")
		return
	}
	defer tx.Rollback()
	var base string
	err = tx.QueryRowContext(ctx, "SELECT url FROM upstreams WHERE id=? AND pool=?", p.ID, p.Previous).Scan(&base)
	if err == sql.ErrNoRows {
		jsonError(w, http.StatusConflict, "号池已变化，请刷新后重试")
		return
	}
	var conflict bool
	if err == nil {
		err = tx.QueryRowContext(ctx, "SELECT EXISTS(SELECT 1 FROM upstreams WHERE url=? AND (pool=? OR (pool=? AND id='openai-env')))", base, p.Name, p.Previous).Scan(&conflict)
	}
	if conflict {
		jsonError(w, http.StatusConflict, "该名称已被使用，或号池包含由 .env 管理的成员")
		return
	}
	if err == nil {
		_, err = tx.ExecContext(ctx, "UPDATE upstreams SET pool=? WHERE url=? AND pool=?", p.Name, base, p.Previous)
	}
	if err == nil {
		// The destination may still have stale assignments after its last member was moved elsewhere.
		_, err = tx.ExecContext(ctx, "DELETE FROM affinity WHERE pool=?", p.Name+"\x00"+base)
	}
	if err == nil {
		_, err = tx.ExecContext(ctx, "UPDATE affinity SET pool=? WHERE pool=?", p.Name+"\x00"+base, p.Previous+"\x00"+base)
	}
	if err != nil || tx.Commit() != nil {
		jsonError(w, http.StatusServiceUnavailable, "无法更新号池名称")
		return
	}
	writeJSON(w, http.StatusOK, object{"ok": true})
}
