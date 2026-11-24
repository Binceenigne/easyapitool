package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"time"
)

type agentProxyLease struct {
	ctx       context.Context
	selected  route
	model     string
	effort    string
	mode      string
	web       bool
	expiresAt time.Time
}

type agentRouteContextKey struct{}

func (g *gateway) authorizeAgentLease(w http.ResponseWriter, r *http.Request) (*agentProxyLease, route, bool) {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	peer, parseErr := netip.ParseAddr(host)
	if err != nil || parseErr != nil || !peer.IsLoopback() {
		openAIError(w, http.StatusForbidden, "internal_only", "内部接口仅允许本机访问")
		return nil, route{}, false
	}
	g.agentMu.Lock()
	lease := g.agentLeases[bearer(r)]
	g.agentMu.Unlock()
	if lease == nil || lease.ctx.Err() != nil || time.Now().After(lease.expiresAt) {
		openAIError(w, http.StatusUnauthorized, "invalid_agent_lease", "Agent 调用授权已过期")
		return nil, route{}, false
	}
	selected := lease.selected
	if selected.key.ID != "" {
		key, anchor, err := g.store.agentKey(r.Context(), selected.key.ID)
		if err == nil {
			selected, err = g.routeForKey(r.Context(), key, anchor, selected.clientIP, true)
		}
		if err != nil {
			g.proxyError(w, err)
			return nil, route{}, false
		}
	}
	return lease, selected, true
}

func (g *gateway) proxyRoute(r *http.Request) (route, error) {
	if selected, ok := r.Context().Value(agentRouteContextKey{}).(route); ok {
		return selected, nil
	}
	return g.resolveRoute(r.Context(), bearer(r), g.clientIP(r), true)
}

func (g *gateway) newAgentLease(ctx context.Context, selected route, model, effort, mode string, web bool) (string, func()) {
	token := "lease-" + randomID(32)
	lease := &agentProxyLease{ctx: ctx, selected: selected, model: model, effort: effort, mode: mode, web: web, expiresAt: time.Now().Add(20 * time.Minute)}
	g.agentMu.Lock()
	if g.agentLeases == nil {
		g.agentLeases = make(map[string]*agentProxyLease)
	}
	for key, current := range g.agentLeases {
		if current.ctx.Err() != nil || time.Now().After(current.expiresAt) {
			delete(g.agentLeases, key)
		}
	}
	g.agentLeases[token] = lease
	g.agentMu.Unlock()
	return token, func() {
		g.agentMu.Lock()
		delete(g.agentLeases, token)
		g.agentMu.Unlock()
	}
}

// Internal leases never carry upstream credentials to Python. Each SDK request
// re-enters the normal proxy, admission ledger and current key/IP checks.
func (g *gateway) agentProxy(w http.ResponseWriter, r *http.Request) {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	peer, parseErr := netip.ParseAddr(host)
	if err != nil || parseErr != nil || !peer.IsLoopback() {
		openAIError(w, http.StatusForbidden, "internal_only", "内部接口仅允许本机访问")
		return
	}
	g.agentMu.Lock()
	lease := g.agentLeases[bearer(r)]
	g.agentMu.Unlock()
	if lease == nil || lease.ctx.Err() != nil || time.Now().After(lease.expiresAt) {
		openAIError(w, http.StatusUnauthorized, "invalid_agent_lease", "Agent 调用授权已过期")
		return
	}
	selected := lease.selected
	if selected.key.ID != "" {
		key, anchor, err := g.store.agentKey(r.Context(), selected.key.ID)
		if err == nil {
			selected, err = g.routeForKey(r.Context(), key, anchor, selected.clientIP, true)
		}
		if err != nil {
			g.proxyError(w, err)
			return
		}
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 16<<20))
	if err != nil {
		openAIError(w, http.StatusRequestEntityTooLarge, "agent_body_too_large", "Agent 请求内容超过上限")
		return
	}
	var fields object
	if json.Unmarshal(data, &fields) != nil || objectString(fields, "model") != lease.model || objectString(asObject(fields["reasoning"]), "effort") != lease.effort {
		openAIError(w, http.StatusForbidden, "agent_lease_scope", "Agent 请求超出授权模型或深度")
		return
	}
	tools, _ := fields["tools"].([]any)
	for _, raw := range tools {
		tool := asObject(raw)
		allowed := lease.web && strings.HasPrefix(objectString(tool, "type"), "web_search")
		if scope, ok := lease.ctx.Value(agentVisualScopeKey{}).(*agentVisualScope); ok && lease.web && objectString(tool, "type") == "function" && oneOf(objectString(tool, "name"), "inspect_visual_references", "select_visual_references") {
			limit, _, policyErr := scope.limits(selected.key)
			allowed = policyErr == nil && limit > 0
			if policyErr == nil && !allowed {
				if selected.key.ID == "" {
					allowed = true
				} else if profile, err := g.modelsForKey(selected.key); err == nil {
					allowed = profile.Tier.WebSearch && profile.Tier.ImageEditing
				}
			}
		}
		if !allowed {
			if scope, ok := lease.ctx.Value(agentVisualScopeKey{}).(*agentVisualScope); ok && scope.workspace != nil && objectString(tool, "type") == "function" && oneOf(objectString(tool, "name"), "list_image_workspace", "view_image_workspace_assets", "select_image_workspace_inputs") {
				allowed = true
			}
		}
		if !allowed {
			openAIError(w, http.StatusForbidden, "agent_lease_scope", "Agent 请求超出授权工具范围")
			return
		}
	}
	if lease.web {
		limit := int64(3)
		if existing, valid := tokenCount(fields["max_tool_calls"]); valid && existing >= 1 && existing < 3 {
			limit = existing
		}
		fields["max_tool_calls"] = limit
		data, _ = json.Marshal(fields)
	}
	ctx, cancel := context.WithCancel(r.Context())
	stop := context.AfterFunc(lease.ctx, cancel)
	defer stop()
	defer cancel()
	ctx = context.WithValue(ctx, agentLeaseContextKey{}, lease)
	r = r.Clone(context.WithValue(ctx, agentRouteContextKey{}, selected))
	r.Body = io.NopCloser(bytes.NewReader(data))
	r.ContentLength = int64(len(data))
	r.Header.Set("X-Mirra-Reasoning-Mode", lease.mode)
	r.Header.Del("X-Real-IP")
	r.Header.Del("X-Forwarded-For")
	g.proxy("/responses")(w, r)
}

func (s *store) agentKey(ctx context.Context, id string) (accessKey, upstream, error) {
	var key accessKey
	var policy string
	err := s.db.QueryRowContext(ctx, `SELECT id,name,prefix,upstream_id,policy,enabled FROM access_keys WHERE id=?
		AND NOT EXISTS(SELECT 1 FROM deleted_keys WHERE key_id=access_keys.id)`, id).
		Scan(&key.ID, &key.Name, &key.Prefix, &key.Upstream, &policy, &key.Enabled)
	if errors.Is(err, sql.ErrNoRows) || err == nil && !key.Enabled {
		return key, upstream{}, errInvalidKey
	}
	if err != nil {
		return key, upstream{}, err
	}
	if err := json.Unmarshal([]byte(policy), &key.Policy); err != nil {
		return key, upstream{}, err
	}
	anchor, err := s.getUpstream(ctx, key.Upstream)
	if errors.Is(err, sql.ErrNoRows) || err == nil && !anchor.Enabled && anchor.Pool == "" {
		return key, anchor, errInvalidKey
	}
	return key, anchor, err
}
