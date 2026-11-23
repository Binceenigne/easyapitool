package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/netip"
	"strings"
	"time"
)

type credentialID struct {
	owner string
	id    string
}

type keyInput struct {
	KeyID    string
	Name     string
	Value    string
	BaseURL  string
	DeviceID string
}

type mobileKey struct {
	keyInput
	usage   object
	models  []string
	lastErr string
	updated time.Time
}

type route struct {
	key      accessKey
	upstream upstream
	clientIP netip.Addr
}

func (m *mobile) resolveKey(ctx context.Context, key keyInput, clientIP netip.Addr, dispatch bool) (route, error) {
	if m.g.managedKey(key) {
		return m.g.resolveRoute(ctx, key.Value, clientIP, dispatch)
	}
	return route{upstream: upstream{URL: key.BaseURL, Secret: key.Value, ID: "legacy"}, clientIP: clientIP}, nil
}

func (m *mobile) parseKey(w http.ResponseWriter, r *http.Request, input *keyInput) bool {
	if !decodeRequest(w, r, 24<<10, input) {
		return false
	}
	input.KeyID = strings.TrimSpace(input.KeyID)
	input.Name = strings.TrimSpace(input.Name)
	input.Value = strings.TrimSpace(input.Value)
	input.DeviceID = strings.TrimSpace(input.DeviceID)
	input.BaseURL = strings.TrimRight(strings.TrimSpace(input.BaseURL), "/")
	if len(input.KeyID) > 120 || len(input.Name) == 0 || len(input.Name) > 120 || !validSecret(input.Value) || len(input.BaseURL) > 2048 {
		jsonError(w, http.StatusBadRequest, "API Key 信息不完整或无效")
		return false
	}
	if m.g.managedKey(*input) {
		input.BaseURL = m.g.cfg.publicBase
		return true
	}
	if strings.HasPrefix(input.Value, "sk-proj-") {
		if input.BaseURL != "" && input.BaseURL != openAIBaseURL {
			jsonError(w, http.StatusBadRequest, "OpenAI 项目密钥须使用官方 API 地址")
			return false
		}
		input.BaseURL = openAIBaseURL
	}
	if input.BaseURL == "" {
		input.BaseURL = m.g.cfg.legacyBase
	}
	base, err := m.g.cfg.baseURL(input.BaseURL)
	if err != nil {
		jsonError(w, http.StatusBadRequest, err.Error())
		return false
	}
	input.BaseURL = base.String()
	return true
}

func validSecret(secret string) bool {
	return len(secret) > 0 && len(secret) <= 4096 && strings.IndexFunc(secret, func(r rune) bool { return r <= ' ' || r > '~' }) == -1
}

func (m *mobile) fetchKey(ctx context.Context, input keyInput, ip netip.Addr) (*mobileKey, error) {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	selected, err := m.resolveKey(ctx, input, ip, false)
	if err != nil {
		return nil, err
	}
	key := &mobileKey{keyInput: input, updated: time.Now(), models: []string{}}
	switch {
	case selected.key.ID != "":
		key.usage, err = m.g.store.usage(ctx, selected.key, time.Now())
		if err == nil {
			key.usage["appModels"], err = m.g.modelsForKey(selected.key)
		}
		if err == nil {
			key.usage["planName"] = key.usage["appModels"].(*modelSettings).Tier.Name
		}
		return key, err
	case input.BaseURL == openAIBaseURL:
		key.usage = object{"isValid": true, "mode": "official", "status": "active"}
	default:
		key.usage, err = m.g.getUpstreamJSON(ctx, selected.upstream, "/usage", "days=30")
		if err != nil {
			return nil, err
		}
	}
	models, err := m.g.getUpstreamJSON(ctx, selected.upstream, "/models", "")
	if err != nil && input.BaseURL == openAIBaseURL {
		return nil, err
	}
	items, _ := models["data"].([]any)
	for _, item := range items {
		id := objectString(asObject(item), "id")
		if id == "" {
			id, _ = item.(string)
		}
		if id != "" && len(id) <= 256 && len(key.models) < 1000 {
			key.models = append(key.models, id)
		}
	}
	return key, nil
}

func (g *gateway) getUpstreamJSON(ctx context.Context, u upstream, path, query string) (object, error) {
	req, err := g.upstreamRequest(ctx, u, http.MethodGet, path, query, nil)
	if err != nil {
		return nil, err
	}
	resp, err := g.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("无法连接上游服务")
	}
	defer resp.Body.Close()
	if err := upstreamStatus(resp); err != nil {
		return nil, err
	}
	var payload object
	if err := json.NewDecoder(io.LimitReader(resp.Body, 2<<20)).Decode(&payload); err != nil || payload == nil {
		return nil, fmt.Errorf("上游返回数据无效")
	}
	return payload, nil
}

func (m *mobile) saveKey(owner string, key *mobileKey) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	id := credentialID{owner, key.KeyID}
	if existing := m.keys[id]; existing != nil {
		if existing.Value == key.Value && existing.BaseURL == key.BaseURL && key.usage == nil {
			key.usage, key.models, key.lastErr = existing.usage, existing.models, existing.lastErr
		}
	} else {
		if len(m.keys) >= 4096 {
			return false
		}
		count := 0
		for k := range m.keys {
			if k.owner == owner {
				count++
			}
		}
		if count >= 64 {
			return false
		}
	}
	m.keys[id] = key
	return true
}

func (m *mobile) registerDevice(w http.ResponseWriter, r *http.Request) {
	var input keyInput
	if !m.parseKey(w, r, &input) {
		return
	}
	if !validDevice(input.DeviceID) || input.KeyID == "" {
		jsonError(w, http.StatusBadRequest, "设备标识或 API Key 编号无效")
		return
	}
	key, err := m.fetchKey(r.Context(), input, m.g.clientIP(r))
	if err != nil {
		jsonError(w, http.StatusUnauthorized, "API Key 验证失败："+err.Error())
		return
	}
	if !m.saveKey("device:"+input.DeviceID, key) {
		jsonError(w, http.StatusTooManyRequests, "保存的密钥数量已达上限")
		return
	}
	writeJSON(w, http.StatusOK, object{"ok": true, "bearerToken": m.issueDeviceToken(input.DeviceID, time.Now()), "expiresIn": int(deviceTTL.Seconds())})
}

func (m *mobile) addKey(w http.ResponseWriter, r *http.Request, owner string) {
	var input keyInput
	if !m.parseKey(w, r, &input) {
		return
	}
	if input.KeyID == "" {
		input.KeyID = randomID(18)
	}
	if !m.saveKey(owner, &mobileKey{keyInput: input, models: []string{}, updated: time.Now()}) {
		jsonError(w, http.StatusTooManyRequests, "保存的密钥数量已达上限")
		return
	}
	writeJSON(w, http.StatusOK, object{"ok": true, "keyId": input.KeyID, "state": m.state(r.Context(), owner, m.g.clientIP(r))})
}

func (m *mobile) deleteKey(w http.ResponseWriter, r *http.Request, owner string) {
	id := credentialID{owner, r.PathValue("id")}
	m.mu.Lock()
	_, ok := m.keys[id]
	delete(m.keys, id)
	m.mu.Unlock()
	result := object{"ok": ok, "state": m.state(r.Context(), owner, m.g.clientIP(r))}
	if !ok {
		result["error"] = "API Key 不存在"
	}
	writeJSON(w, http.StatusOK, result)
}

func (m *mobile) getState(w http.ResponseWriter, r *http.Request, owner string) {
	writeJSON(w, http.StatusOK, m.state(r.Context(), owner, m.g.clientIP(r)))
}

func (m *mobile) state(ctx context.Context, owner string, ip netip.Addr) object {
	m.mu.Lock()
	keys := make([]*mobileKey, 0)
	active := []string{}
	for id, key := range m.keys {
		if id.owner == owner {
			keys = append(keys, key)
		}
	}
	for id, job := range m.tasks {
		if id.owner == owner && job.cancel != nil {
			active = append(active, id.id)
		}
	}
	m.mu.Unlock()
	items := make([]object, 0, len(keys))
	for _, key := range keys {
		copy := *key
		if key.BaseURL == m.g.cfg.publicBase {
			updated, err := m.fetchKey(ctx, key.keyInput, ip)
			if err == nil {
				copy.usage = updated.usage
			} else {
				copy.usage, copy.lastErr = nil, err.Error()
			}
		}
		view := copy.state()
		if m.g.managedKey(key.keyInput) && copy.usage != nil {
			view["appModels"] = copy.usage["appModels"]
		}
		items = append(items, view)
	}
	return object{"appName": appName, "keys": items, "activeTasks": active, "storageMode": "android-filesystem-sqlite-index", "serverPersistence": false,
		"usagePersistence": "server-sqlite", "features": object{"serverQuota": true, "nativeImageOptions": true, "svg": m.g.cfg.svgEnabled}}
}

func (m *mobile) refresh(w http.ResponseWriter, r *http.Request, owner string) {
	m.mu.Lock()
	keys := []*mobileKey{}
	for id, key := range m.keys {
		if id.owner == owner {
			keys = append(keys, key)
		}
	}
	m.mu.Unlock()
	refreshed, failed := []string{}, []string{}
	ctx, cancel := context.WithTimeout(r.Context(), 40*time.Second)
	defer cancel()
	for _, key := range keys {
		if ctx.Err() != nil {
			failed = append(failed, key.KeyID)
			continue
		}
		updated, err := m.fetchKey(ctx, key.keyInput, m.g.clientIP(r))
		if err != nil {
			failed = append(failed, key.KeyID)
			copy := *key
			copy.lastErr = err.Error()
			updated = &copy
		} else {
			refreshed = append(refreshed, key.KeyID)
		}
		id := credentialID{owner, key.KeyID}
		m.mu.Lock()
		if m.keys[id] == key {
			m.keys[id] = updated
		}
		m.mu.Unlock()
	}
	writeJSON(w, http.StatusOK, object{"ok": true, "valid": len(refreshed) > 0 && len(failed) == 0, "refreshed": refreshed, "failed": failed,
		"state": m.state(r.Context(), owner, m.g.clientIP(r))})
}

func (k mobileKey) state() object {
	payload := k.usage
	window := func(name string) object {
		var item object
		if items, ok := payload["rate_limits"].([]object); ok {
			for _, candidate := range items {
				if candidate["window"] == name {
					item = candidate
					break
				}
			}
		} else if items, ok := payload["rate_limits"].([]any); ok {
			for _, candidate := range items {
				if asObject(candidate)["window"] == name {
					item = asObject(candidate)
					break
				}
			}
		}
		unit := item["unit"]
		if unit == nil && payload["mode"] == "request_quota" {
			unit = "requests"
		}
		return object{"remainingPercent": item["remainingPercent"], "severity": item["severity"], "limit": getNumber(item["limit"]), "used": getNumber(item["used"]), "remaining": item["remaining"], "unit": unit,
			"resetTime": item["reset_at"], "windowStart": item["window_start"], "limitChange": asObject(payload["_limit_changes"])[name]}
	}
	quota, usage := asObject(payload["quota"]), asObject(payload["usage"])
	today, total := asObject(usage["today"]), asObject(usage["total"])
	limit, used, remaining := getNumber(quota["limit"]), getNumber(quota["used"]), getNumber(quota["remaining"])
	if limit <= 0 && payload["balance"] != nil {
		remaining, used = getNumber(payload["balance"]), getNumber(total["cost"])
		limit = remaining + used
	}
	expires := objectString(payload, "expires_at")
	if expires == "" {
		expires = objectString(asObject(payload["subscription"]), "expires_at")
	}
	expiresAt := int64(0)
	if parsed, err := time.Parse(time.RFC3339Nano, expires); err == nil {
		expiresAt = parsed.UnixMilli()
	}
	status := objectString(payload, "status")
	if status == "" {
		status = "unknown"
		if payload["isValid"] == true {
			status = "active"
		}
	}
	if k.lastErr != "" {
		status = "error"
	}
	masked := strings.Repeat("*", len(k.Value))
	if len(k.Value) > 8 {
		masked = k.Value[:4] + "..." + k.Value[len(k.Value)-4:]
	}
	return object{"id": k.KeyID, "name": k.Name, "value": masked, "baseUrl": k.BaseURL, "status": status,
		"serverQuota": payload["serverQuota"], "rates": payload["rates"], "rateLimit": payload["rateLimit"], "quotaEvents": payload["quotaEvents"], "quotaRevision": payload["quotaRevision"], "quotaSeverity": quota["severity"], "quotaPercent": quota["remainingPercent"], "quotaLimitChange": asObject(payload["_limit_changes"])["quota"], "mode": payload["mode"], "planName": payload["planName"], "expireDateStr": expires, "expireTimestamp": expiresAt,
		"totalQuota": limit, "usedQuota": used, "remainingQuota": remaining, "quotaUnit": payload["quotaUnit"],
		"win5h": window("5h"), "win1d": window("1d"), "win7d": window("7d"),
		"todayRequests": today["requests"], "totalRequests": total["requests"], "todayCost": today["cost"], "totalCost": total["cost"],
		"hasPricedUsage": total["cost"] != nil || !oneOf(objectString(payload, "mode"), "request_quota", "usd_quota", "official"),
		"todayTokens":    today["total_tokens"], "totalTokens": total["total_tokens"], "modelsCount": len(k.models), "models": k.models,
		"lastError": k.lastErr}
}

func asObject(value any) object {
	item, _ := value.(object)
	return item
}

func objectString(value object, key string) string {
	str, _ := value[key].(string)
	return str
}

func getNumber(value any) float64 {
	switch value := value.(type) {
	case float64:
		return value
	case int64:
		return float64(value)
	case int:
		return float64(value)
	default:
		return 0
	}
}

func (m *mobile) getKey(owner, keyID string) (keyInput, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	key := m.keys[credentialID{owner, keyID}]
	if key == nil {
		return keyInput{}, errors.New("请选择有效的 API Key")
	}
	return key.keyInput, nil
}

func (g *gateway) notifyQuota(id string, snapshot object) {
	if g.onQuotaChange != nil {
		revision, _ := snapshot["quotaRevision"].(int64)
		g.onQuotaChange(id, revision)
	}
}

// Notify owners of registered copies. The subsequent state request performs the usual IP and key checks.
func (m *mobile) quotaChanged(id string, revision int64) {
	var digest string
	if err := m.g.store.db.QueryRowContext(m.ctx, "SELECT digest FROM access_keys WHERE id=?", id).Scan(&digest); err != nil {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	for device, key := range m.keys {
		if key.BaseURL == m.g.cfg.publicBase && keyDigest(key.Value) == digest {
			m.emitLocked(device.owner, object{"type": "quota_changed", "keyId": key.KeyID, "revision": revision})
		}
	}
}

func (m *mobile) modelsChanged(revision int64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	owners := make(map[string]bool)
	for device, key := range m.keys {
		if m.g.managedKey(key.keyInput) && !owners[device.owner] {
			owners[device.owner] = true
			m.emitLocked(device.owner, object{"type": "models_changed", "revision": revision})
		}
	}
}
