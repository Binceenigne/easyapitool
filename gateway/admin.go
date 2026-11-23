package main

import (
	"context"
	"crypto/subtle"
	"embed"
	"errors"
	"io/fs"
	"net"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

//go:embed web/*
var adminFiles embed.FS

type admin struct {
	gateway    *gateway
	mu         sync.Mutex
	sessions   map[string]time.Time
	failures   int
	windowEnds time.Time
}

func newAdmin(g *gateway) *admin {
	return &admin{gateway: g, sessions: make(map[string]time.Time)}
}

func (a *admin) routes(m *mobile) http.Handler {
	mux := http.NewServeMux()
	web, err := fs.Sub(adminFiles, "web")
	if err != nil {
		panic(err)
	}
	files := http.FileServerFS(web)
	for _, path := range []string{"/{$}", "/admin.css", "/admin.js", "/admin-models.js", "/admin-tiers.js", "/icons.svg", "/mirra.png"} {
		mux.Handle("GET "+path, files)
	}
	// Existing public deployments forward /admin/ while allowing only the older
	// root asset names. Serve the new workspace asset through that namespace.
	mux.Handle("GET /admin/assets/admin-tiers.js", http.StripPrefix("/admin/assets", files))
	mux.Handle("GET /admin/assets/admin-tier-publish.js", http.StripPrefix("/admin/assets", files))
	if m != nil {
		mux.HandleFunc("GET /app-preview/{asset...}", m.appPreview)
	}
	mux.HandleFunc("POST /admin/login", a.login)
	mux.HandleFunc("POST /admin/logout", a.protected(a.logout))
	mux.HandleFunc("GET /admin/state", a.protected(a.state))
	mux.HandleFunc("POST /admin/pools", a.protected(a.createPool))
	mux.HandleFunc("POST /admin/pools/rename", a.protected(a.renamePool))
	mux.HandleFunc("POST /admin/proxy", a.protected(a.configureProxy))
	mux.HandleFunc("POST /admin/proxy/test", a.protected(a.testProxy))
	mux.HandleFunc("POST /admin/upstreams", a.protected(a.saveUpstream))
	mux.HandleFunc("POST /admin/upstreams/{id}/test", a.protected(a.testUpstream))
	mux.HandleFunc("GET /admin/upstreams/{id}/models", a.protected(a.discoverModels))
	mux.HandleFunc("POST /admin/models", a.protected(a.configureModels))
	mux.HandleFunc("POST /admin/keys/{id}/reset", a.protected(a.resetQuota))
	mux.HandleFunc("POST /admin/keys", a.protected(a.saveKey))
	mux.HandleFunc("POST /admin/keys/{id}/reveal", a.protected(a.revealKey))
	mux.HandleFunc("POST /admin/keys/{id}/delete", a.protected(a.deleteKey))
	mux.HandleFunc("POST /admin/tiers", a.protected(a.saveTier))
	mux.HandleFunc("GET /admin/tiers/{id}/preview", a.protected(a.previewTier))
	mux.HandleFunc("POST /admin/landing-tiers", a.protected(a.saveLandingTiers))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host, _, err := net.SplitHostPort(r.Host)
		if err != nil {
			host = strings.Trim(r.Host, "[]")
		}
		if !(strings.EqualFold(host, "localhost") || net.ParseIP(host).IsLoopback()) {
			jsonError(w, http.StatusForbidden, "管理页只允许本地访问")
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != "http://"+r.Host {
			jsonError(w, http.StatusForbidden, "管理请求来源无效")
			return
		}
		w.Header().Set("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Cache-Control", "no-store")
		mux.ServeHTTP(w, r)
	})
}

const sessionCookie = "mirra_admin"
const sessionTTL = 12 * time.Hour

func (a *admin) login(w http.ResponseWriter, r *http.Request) {
	var input struct{ PIN string }
	if !decodeRequest(w, r, 2048, &input) {
		return
	}
	now := time.Now()
	a.mu.Lock()
	defer a.mu.Unlock()
	if now.After(a.windowEnds) {
		a.failures, a.windowEnds = 0, now.Add(5*time.Minute)
	}
	if a.failures >= 8 {
		jsonError(w, http.StatusTooManyRequests, "PIN 尝试次数过多，请稍后再试")
		return
	}
	if subtle.ConstantTimeCompare([]byte(input.PIN), []byte(a.gateway.cfg.pin)) != 1 {
		a.failures++
		jsonError(w, http.StatusUnauthorized, "PIN 错误")
		return
	}
	a.failures = 0
	for token, expiry := range a.sessions {
		if now.After(expiry) {
			delete(a.sessions, token)
		}
	}
	if previous, err := r.Cookie(sessionCookie); err == nil {
		delete(a.sessions, previous.Value)
	}
	if len(a.sessions) >= 128 {
		jsonError(w, http.StatusTooManyRequests, "登录会话过多")
		return
	}
	token := randomID(32)
	a.sessions[token] = now.Add(sessionTTL)
	http.SetCookie(w, &http.Cookie{Name: sessionCookie, Value: token, HttpOnly: true, SameSite: http.SameSiteStrictMode, Path: "/", MaxAge: int(sessionTTL.Seconds())})
	writeJSON(w, http.StatusOK, object{"ok": true})
}

func (a *admin) protected(handler http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if cookie, err := r.Cookie(sessionCookie); err == nil {
			a.mu.Lock()
			valid := time.Now().Before(a.sessions[cookie.Value])
			a.mu.Unlock()
			if valid {
				handler(w, r)
				return
			}
		}
		jsonError(w, http.StatusUnauthorized, "请先登录")
	}
}

func (a *admin) logout(w http.ResponseWriter, r *http.Request) {
	cookie, _ := r.Cookie(sessionCookie)
	a.mu.Lock()
	delete(a.sessions, cookie.Value)
	a.mu.Unlock()
	http.SetCookie(w, &http.Cookie{Name: sessionCookie, MaxAge: -1, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode})
	writeJSON(w, http.StatusOK, object{"ok": true})
}

func (a *admin) state(w http.ResponseWriter, r *http.Request) {
	s := a.gateway.store
	upstreams, err := s.listUpstreams(r.Context())
	if err != nil {
		jsonError(w, http.StatusServiceUnavailable, "无法读取上游设置")
		return
	}
	keys, err := s.listKeys(r.Context())
	if err != nil {
		jsonError(w, http.StatusServiceUnavailable, "无法读取密钥列表")
		return
	}
	usage := object{}
	for _, k := range keys {
		item, err := s.usage(r.Context(), k, time.Now())
		if err != nil {
			jsonError(w, http.StatusServiceUnavailable, "无法读取用量")
			return
		}
		usage[k.ID] = item
	}
	hosts := make([]string, 0, len(a.gateway.cfg.allowedHosts))
	for host := range a.gateway.cfg.allowedHosts {
		hosts = append(hosts, host)
	}
	sort.Strings(hosts)
	landing, err := a.gateway.landingTiers(r.Context())
	if err != nil {
		jsonError(w, http.StatusServiceUnavailable, "无法读取落地页档位设置")
		return
	}
	writeJSON(w, http.StatusOK, object{
		"upstreams": upstreams, "keys": keys, "usage": usage, "proxy": a.gateway.egress.view(),
		"appModels":    a.gateway.appModels(),
		"landingTiers": landing.adminView(),
		"gateway": object{"publicBaseUrl": a.gateway.cfg.publicBase, "allowedHosts": hosts,
			"reserveMicroUSD": a.gateway.cfg.reserveMicroUSD, "pricingReady": a.gateway.cfg.reserveMicroUSD > 0 && len(a.gateway.cfg.pricing) > 0},
	})
}

func (a *admin) saveUpstream(w http.ResponseWriter, r *http.Request) {
	var input struct {
		upstream
		APIKey string `json:"apiKey"`
	}
	if !decodeRequest(w, r, 16<<10, &input) {
		return
	}
	u := input.upstream
	u.Name = strings.TrimSpace(u.Name)
	u.Pool = strings.TrimSpace(u.Pool)
	u.Secret = strings.TrimSpace(input.APIKey)
	base, err := a.gateway.cfg.baseURL(u.URL)
	if err != nil || len(u.Name) == 0 || len(u.Name) > 120 || (u.Secret != "" && !validSecret(u.Secret)) ||
		len(u.URL) > 2048 || len(u.ID) > 40 || !validPool(u.Pool) || (strings.HasPrefix(u.Secret, "sk-proj-") && base.String() != openAIBaseURL) {
		jsonError(w, http.StatusBadRequest, "请填写名称、受允许的 HTTPS 上游地址和有效密钥")
		return
	}
	if u.ID == "openai-env" {
		jsonError(w, http.StatusBadRequest, "请在 .env 中修改此上游")
		return
	}
	u.URL = base.String()
	if u.ID == "" {
		u.ID = randomID(15)
	}
	if err := a.gateway.store.saveUpstream(r.Context(), u); err != nil {
		jsonError(w, http.StatusBadRequest, "无法保存上游。新增上游或修改地址时需要填写 API Key")
		return
	}
	writeJSON(w, http.StatusOK, u)
}

func (a *admin) testUpstream(w http.ResponseWriter, r *http.Request) {
	g := a.gateway
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	u, err := g.store.getUpstream(ctx, r.PathValue("id"))
	if err != nil {
		jsonError(w, http.StatusNotFound, "上游不存在或配置不可用")
		return
	}
	req, err := g.upstreamRequest(ctx, u, http.MethodGet, "/models", "", nil)
	if err != nil {
		jsonError(w, http.StatusBadRequest, "上游地址配置无效")
		return
	}
	resp, err := g.client.Do(req)
	if err != nil {
		message := "无法连接上游"
		if errors.Is(err, context.DeadlineExceeded) {
			message = "连接上游超时"
		}
		jsonError(w, http.StatusBadGateway, message)
		return
	}
	resp.Body.Close()
	writeJSON(w, http.StatusOK, object{"ok": resp.StatusCode >= 200 && resp.StatusCode < 300, "upstreamStatus": resp.StatusCode})
}

func (a *admin) saveKey(w http.ResponseWriter, r *http.Request) {
	var input struct {
		accessKey
		Custom          string             `json:"customKey"`
		Revision        *int64             `json:"revision"`
		UseTierDefaults tierDefaultsChoice `json:"useTierDefaults"`
	}
	input.Enabled = true
	if !decodeRequest(w, r, 16<<10, &input) {
		return
	}
	k := input.accessKey
	a.gateway.modelMu.Lock()
	defer a.gateway.modelMu.Unlock()
	if k.Policy.TierID == "" {
		k.Policy.TierID = a.gateway.appModels().defaultTierID()
		if k.Policy.TierID == "" {
			jsonError(w, http.StatusBadRequest, "尚未创建功能档位，请先创建档位后再签发密钥")
			return
		}
	}
	tier, ok := a.gateway.appModels().tier(k.Policy.TierID)
	if !ok {
		jsonError(w, 400, "请选择有效的功能档位")
		return
	}
	now := time.Now().UTC()
	if input.UseTierDefaults {
		k.Policy = tier.DefaultQuota.apply(k.Policy, now)
	}
	k.Name = strings.TrimSpace(k.Name)
	if len(k.Name) == 0 || len(k.Name) > 120 || len(k.ID) > 40 {
		jsonError(w, http.StatusBadRequest, "请填写有效的密钥名称")
		return
	}
	if err := k.Policy.validate(); err != nil {
		jsonError(w, http.StatusBadRequest, err.Error())
		return
	}
	if k.Enabled && k.Policy.usdLimited() && (a.gateway.cfg.reserveMicroUSD == 0 || len(a.gateway.cfg.pricing) == 0) {
		jsonError(w, http.StatusBadRequest, errPricingUnavailable.Error())
		return
	}
	if input.Custom != "" && (k.ID != "" || !validCustomKey(input.Custom)) {
		jsonError(w, http.StatusBadRequest, "自定义密钥须填写 16 至 128 位字母、数字、下划线或连字符")
		return
	}
	s := a.gateway.store
	if _, err := s.getUpstream(r.Context(), k.Upstream); err != nil {
		jsonError(w, http.StatusBadRequest, "请选择已配置的上游")
		return
	}
	if k.ID != "" {
		usage, err := s.changeKey(r.Context(), k.ID, &k, "", input.Revision, now)
		if err != nil {
			quotaMutationError(w, err)
			return
		}
		a.gateway.notifyQuota(k.ID, usage)
		writeJSON(w, http.StatusOK, object{"ok": true, "action": "key_updated", "usage": usage})
		return
	}
	key, secret, snapshot, err := s.issue(r.Context(), k, input.Custom)
	if err != nil {
		jsonError(w, http.StatusConflict, "签发密钥失败。请确认自定义密钥未被使用")
		return
	}
	writeJSON(w, http.StatusCreated, object{"ok": true, "key": key, "secret": secret, "action": "key_issued", "usage": snapshot})
}

func validCustomKey(key string) bool {
	if len(key) < 16 || len(key) > 128 {
		return false
	}
	for _, c := range key {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
			return false
		}
	}
	return true
}
