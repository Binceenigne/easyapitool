package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestWindowActionsAndUsageHistory(t *testing.T) {
	g := testGateway(t)
	u := addUpstream(t, g, "meter", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "workspace", Upstream: u.ID, Policy: keyPolicy{Total: 10000, H5: 3000, D1: 4000, D7: 6000}})
	ctx := t.Context()
	now := time.Date(2026, 11, 19, 22, 0, 0, 0, billingZone)
	// Old request-count columns retain their schema but must never affect admission.
	if _, err := g.store.db.Exec("UPDATE access_keys SET limit_5h=1,limit_7d=1 WHERE id=?", k.ID); err != nil {
		t.Fatal(err)
	}
	selected := route{key: k, upstream: u}
	charge := func(at time.Time, cost int64) int64 {
		t.Helper()
		id, err := g.store.reserve(ctx, selected, at, 100)
		if err != nil {
			t.Fatal(err)
		}
		if err := g.store.finish(ctx, id, 200, nil, &cost); err != nil {
			t.Fatal(err)
		}
		return id
	}
	for i := 0; i < 12; i++ {
		charge(now.Add(time.Duration(i)*time.Minute), 100)
	}
	charge(now.Add(20*time.Minute), 1800)
	_, err := g.store.reserve(ctx, selected, now.Add(21*time.Minute), 100)
	var limit *quotaError
	if !errors.As(err, &limit) || !limit.resetAt.Equal(now.Add(5*time.Hour)) {
		t.Fatalf("5h reset: %v", err)
	}
	read := func(at time.Time) object {
		t.Helper()
		data, err := g.store.usage(ctx, k, at)
		if err != nil {
			t.Fatal(err)
		}
		return data
	}
	data := read(now.Add(22 * time.Minute))
	window := func(v object, id string) object { return asObject(asObject(v["usdBudget"])[id]) }
	if got := window(data, "5h"); got["unit"] != "USD" || got["remainingPercent"] != float64(0) || got["severity"] != 3 {
		t.Fatalf("wrong feedback: %v", got)
	}
	if got := window(read(now.Add(2*time.Hour)), "1d")["used"]; got != float64(0) {
		t.Fatalf("day failed to reset at Shanghai midnight: %v", got)
	}
	at := now.Add(2*time.Hour + time.Minute)
	k.Policy.H5 = 5000
	data, err = g.store.changeKey(ctx, k.ID, &k, "", nil, at)
	if err != nil {
		t.Fatal(err)
	}
	if window(data, "5h")["used"] != 0.003 || window(data, "5h")["remaining"] != 0.002 {
		t.Fatal("editing a limit must retain its usage")
	}
	revision := data["quotaRevision"].(int64)
	pending, err := g.store.reserve(ctx, selected, at, 100)
	if err != nil {
		t.Fatal(err)
	}
	data, err = g.store.changeKey(ctx, k.ID, nil, "windows", &revision, at)
	if err != nil {
		t.Fatal(err)
	}
	if window(data, "5h")["used"] != float64(0) || window(data, "7d")["window_start"] != nil || window(data, "total")["used"] != 0.0031 {
		t.Fatal("reset modified a different budget or left a stale window")
	}
	if _, err = g.store.changeKey(ctx, k.ID, nil, "windows", &revision, at); !errors.Is(err, errQuotaConflict) {
		t.Fatal("stale reset accepted")
	}
	late := int64(700)
	if err := g.store.finish(ctx, pending, 200, nil, &late); err != nil {
		t.Fatal(err)
	}
	charge(at, 200)
	data = read(at)
	if window(data, "5h")["used"] != 0.0002 || asObject(asObject(data["usage"])["total"])["settledMicroUSD"] != int64(3900) {
		t.Fatal("late settlement contaminated the reset budget or lost history")
	}
	data, err = g.store.changeKey(ctx, k.ID, nil, "total", nil, at)
	if err != nil {
		t.Fatal(err)
	}
	if window(data, "total")["used"] != float64(0) || window(data, "5h")["used"] != 0.0002 {
		t.Fatal("total reset changed the period")
	}
	data = read(at.Add(5 * time.Hour))
	if window(data, "5h")["used"] != float64(0) || window(data, "7d")["used"] != 0.0002 {
		t.Fatal("automatic refresh removed unrelated history")
	}
	events := data["quotaEvents"].([]quotaEventView)
	if len(events) < 6 || events[0].Kind != "window_refreshed" || data["quotaRevision"] != events[0].ID {
		t.Fatalf("missing durable quota events: %v", events)
	}
	if g.store == nil || asObject(data["rates"])["source"] != "gateway" {
		t.Fatal("server statistics missing")
	}
}

func TestCustomProxyPersistsWithoutExposingCredentials(t *testing.T) {
	g := testGateway(t)
	ctx := t.Context()
	var calls atomic.Int32
	p := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Host != "api.openai.com" || r.Header.Get("Proxy-Authorization") == "" {
			t.Error("proxy credential or destination missing")
		}
		calls.Add(1)
		io.WriteString(w, "via test proxy")
	}))
	defer p.Close()
	raw := strings.Replace(p.URL, "http://", "http://demo:secret@", 1)
	saved, err := g.egress.save(ctx, proxySettings{Mode: "custom", Scope: "openai", URL: raw})
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(saved)
	if bytes.Contains(data, []byte("secret")) || bytes.Contains(data, []byte("demo")) || saved["address"] != p.URL {
		t.Fatal("proxy URL was not masked")
	}
	var sealed []byte
	if err := g.store.db.QueryRow("SELECT value FROM gateway_settings WHERE name='egress'").Scan(&sealed); err != nil || bytes.Contains(sealed, []byte("secret")) {
		t.Fatal("proxy credentials were not encrypted")
	}
	m, err := loadEgress(ctx, g.store, g.cfg)
	if err != nil {
		t.Fatal(err)
	}
	client := newUpstreamClient(g.cfg, m)
	resp, err := client.Get("http://api.openai.com/models")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	direct := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Proxy-Authorization") != "" {
			t.Error("proxy credentials leaked to upstream")
		}
		w.WriteHeader(204)
	}))
	defer direct.Close()
	resp, err = client.Get(direct.URL)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 204 || calls.Load() != 1 {
		t.Fatal("domain scope was not respected")
	}
	if _, err := m.save(ctx, proxySettings{Mode: "direct"}); !errors.Is(err, errProxyChanged) {
		t.Fatal("stale proxy settings accepted")
	}
	if _, err := m.save(ctx, proxySettings{Mode: "custom", Scope: "all", Revision: 1}); err != nil {
		t.Fatal("saved credentials were not reused")
	}
	if _, err := m.save(ctx, proxySettings{Mode: "direct", Revision: 2}); err != nil {
		t.Fatal(err)
	}
	resp, err = client.Get(direct.URL)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if m.view()["address"] != "" || calls.Load() != 1 {
		t.Fatal("direct mode did not take effect")
	}
}

func TestBundledModelRates(t *testing.T) {
	cards, err := loadPricing("openai-standard.json")
	if err != nil {
		t.Fatal(err)
	}
	cfg := config{pricing: cards}
	usage := object{"input_tokens": float64(12000), "output_tokens": float64(0), "input_tokens_details": object{
		"cached_tokens": float64(4000), "cache_write_tokens": float64(7000), "cache_write_tokens_details": object{"ttl_30m_tokens": float64(3000), "ttl_12h_tokens": float64(4000)}}}
	amount := cfg.price("gpt-6.1-terra", false, usageMeter(usage, nil))
	if amount == nil || *amount != 14950 {
		t.Fatalf("incorrect cache-write charge: %v", amount)
	}
	usage["input_tokens"] = float64(300000)
	usage["output_tokens"] = float64(2000)
	amount = cfg.price("gpt-6.1-terra", false, usageMeter(usage, nil))
	if amount == nil || *amount != 620900 {
		t.Fatalf("unexpected long-context charge: %v", amount)
	}
}

func TestAdminPoolAndResetActions(t *testing.T) {
	g := testGateway(t)
	admin := newAdmin(g).routes()
	post := func(path, body string, cookie *http.Cookie) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "http://127.0.0.1"+path, strings.NewReader(body))
		if cookie != nil {
			req.AddCookie(cookie)
		}
		w := httptest.NewRecorder()
		admin.ServeHTTP(w, req)
		return w
	}
	for _, path := range []string{"/admin/pools", "/admin/proxy", "/admin/keys/id/reset"} {
		if post(path, "{}", nil).Code != 401 {
			t.Fatal("unprotected admin action")
		}
	}
	cookie := post("/admin/login", `{"pin":"362514"}`, nil).Result().Cookies()[0]
	group := `{"name":"production","baseUrl":"https://api.openai.com/v1","members":[{"name":"A","apiKey":"fixture-A"},{"name":"B","apiKey":"fixture-B"}]}`
	if w := post("/admin/pools", strings.Replace(group, "fixture-B", "fixture-A", 1), cookie); w.Code != 400 {
		t.Fatal("invalid pool accepted")
	}
	items, _ := g.store.listUpstreams(t.Context())
	if len(items) != 0 {
		t.Fatal("partially committed invalid pool")
	}
	w := post("/admin/pools", group, cookie)
	if w.Code != 201 || strings.Contains(w.Body.String(), "fixture-") {
		t.Fatal("pool creation failed or leaked secrets")
	}
	if post("/admin/pools", group, cookie).Code != 409 {
		t.Fatal("duplicate group was accepted")
	}
	items, _ = g.store.listUpstreams(t.Context())
	k, secret := issue(t, g, accessKey{Name: "test", Upstream: items[0].ID})
	w = post("/admin/keys/"+k.ID+"/reset", `{"scope":"total","revision":0}`, cookie)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"kind":"usage_reset"`) || !strings.Contains(w.Body.String(), `"quotaUnit":"USD"`) {
		t.Fatal("reset response missing authoritative result")
	}
	mux := http.NewServeMux()
	g.registerProxy(mux)
	req := httptest.NewRequestWithContext(context.Background(), "GET", "/v1/usage/events?after=0", nil)
	req.Header.Set("Authorization", "Bearer "+secret)
	out := httptest.NewRecorder()
	mux.ServeHTTP(out, req)
	if out.Code != 200 || !strings.Contains(out.Body.String(), "nextCursor") {
		t.Fatal("key-scoped event feed unavailable")
	}
}
