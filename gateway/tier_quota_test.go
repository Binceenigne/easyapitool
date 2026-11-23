package main

import (
	"encoding/json"
	"maps"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"testing"
	"time"
)

type tierAdminTestClient struct {
	handler http.Handler
	cookie  *http.Cookie
}

func newTierAdminTestClient(t *testing.T, g *gateway) tierAdminTestClient {
	t.Helper()
	client := tierAdminTestClient{handler: newAdmin(g).routes(nil)}
	w := client.request(t, "POST", "/admin/login", `{"pin":"362514"}`)
	if w.Code != http.StatusOK || len(w.Result().Cookies()) != 1 {
		t.Fatal("cannot create test PIN session")
	}
	client.cookie = w.Result().Cookies()[0]
	return client
}

func (c tierAdminTestClient) request(t *testing.T, method, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var data string
	if raw, ok := body.(string); ok {
		data = raw
	} else if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		data = string(encoded)
	}
	r := httptest.NewRequest(method, "http://localhost"+path, strings.NewReader(data))
	if c.cookie != nil {
		r.AddCookie(c.cookie)
	}
	w := httptest.NewRecorder()
	c.handler.ServeHTTP(w, r)
	return w
}

func readTierKey(t *testing.T, g *gateway, id string) accessKey {
	t.Helper()
	keys, err := g.store.listKeys(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range keys {
		if key.ID == id {
			return key
		}
	}
	t.Fatal("test key not found")
	return accessKey{}
}

func TestTierDefaultsAreAuthoritativeAndOnlyAppliedExplicitly(t *testing.T) {
	g := testGateway(t)
	g.cfg.reserveMicroUSD = 100
	g.cfg.pricing = map[string]priceCard{"fixture": {Output: 1}}
	u := addUpstream(t, g, "tier-defaults", "", openAIBaseURL)
	tier := limitedTier()
	tier.DefaultQuota = tierDefaultQuota{ExpiresInDays: 30, Total: 50_000_000, H5: 10_000_000, D1: 20_000_000, D7: 40_000_000}
	installTier(t, g, tier)
	client := newTierAdminTestClient(t, g)
	old := time.Now().Add(-24 * time.Hour)
	input := object{"name": "套餐", "upstreamId": u.ID, "useTierDefaults": true,
		"policy": keyPolicy{TierID: tier.ID, IPs: []string{"127.0.0.1"}, ExpiresAt: &old, Total: 1, H5: 2, D1: 3, D7: 4}}
	before := time.Now().UTC()
	w := client.request(t, "POST", "/admin/keys", input)
	var issued struct {
		Key accessKey `json:"key"`
	}
	if w.Code != http.StatusCreated || json.Unmarshal(w.Body.Bytes(), &issued) != nil {
		t.Fatalf("issue: %d %s", w.Code, w.Body.String())
	}
	k := issued.Key
	if k.Policy.Total != tier.DefaultQuota.Total || k.Policy.H5 != tier.DefaultQuota.H5 ||
		k.Policy.D1 != tier.DefaultQuota.D1 || k.Policy.D7 != tier.DefaultQuota.D7 ||
		k.Policy.TierID != tier.ID || len(k.Policy.IPs) != 1 || k.Policy.IPs[0] != "127.0.0.1/32" {
		t.Fatal("server did not apply all defaults while preserving tier and IP policy")
	}
	if k.Policy.ExpiresAt == nil || k.Policy.ExpiresAt.Before(before.Add(30*24*time.Hour)) ||
		k.Policy.ExpiresAt.After(time.Now().UTC().Add(30*24*time.Hour)) {
		t.Fatal("default expiry did not start at issuance")
	}
	chargeID, err := g.store.reserve(t.Context(), route{key: k, upstream: u, clientIP: netip.MustParseAddr("127.0.0.1")}, time.Now(), 100)
	if err != nil {
		t.Fatal(err)
	}
	charge := int64(250_000)
	if err := g.store.finish(t.Context(), chargeID, 200, nil, &charge); err != nil {
		t.Fatal(err)
	}

	settings := *g.appModels()
	settings.Tiers = append([]featureTier{}, settings.Tiers...)
	for i := range settings.Tiers {
		if settings.Tiers[i].ID == tier.ID {
			settings.Tiers[i].DefaultQuota = tierDefaultQuota{ExpiresInDays: 10, Total: 90_000_000, H5: 20_000_000, D1: 30_000_000, D7: 60_000_000}
			tier = settings.Tiers[i]
		}
	}
	if _, err := g.saveModels(t.Context(), settings); err != nil {
		t.Fatal(err)
	}
	unchanged := readTierKey(t, g, k.ID)
	if unchanged.Policy.encode() != k.Policy.encode() {
		t.Fatal("editing tier defaults silently changed an existing key")
	}

	// Leaving the checkbox off preserves explicitly submitted key settings.
	manual := k
	manual.Policy.Total = 70_000_000
	w = client.request(t, "POST", "/admin/keys", object{"id": manual.ID, "name": manual.Name,
		"upstreamId": manual.Upstream, "enabled": true, "policy": manual.Policy, "useTierDefaults": false})
	if w.Code != http.StatusOK || readTierKey(t, g, k.ID).Policy.Total != manual.Policy.Total {
		t.Fatal("unchecked tier defaults overwrote manual limits")
	}

	// Applying again changes only the policy, never already settled charges.
	before = time.Now().UTC()
	apply := object{"id": k.ID, "name": k.Name, "upstreamId": k.Upstream, "enabled": true,
		"policy": k.Policy, "useTierDefaults": true}
	w = client.request(t, "POST", "/admin/keys", apply)
	if w.Code != http.StatusOK {
		t.Fatalf("edit apply: %d %s", w.Code, w.Body.String())
	}
	updated := readTierKey(t, g, k.ID)
	if updated.Policy.Total != tier.DefaultQuota.Total || updated.Policy.H5 != tier.DefaultQuota.H5 ||
		updated.Policy.D1 != tier.DefaultQuota.D1 || updated.Policy.D7 != tier.DefaultQuota.D7 ||
		updated.Policy.ExpiresAt.Before(before.Add(10*24*time.Hour)) ||
		updated.Policy.ExpiresAt.After(time.Now().UTC().Add(10*24*time.Hour)) {
		t.Fatal("editing a key did not apply current server defaults")
	}
	usage, err := g.store.usage(t.Context(), updated, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	for _, window := range []string{"total", "5h", "1d", "7d"} {
		if asObject(asObject(usage["usdBudget"])[window])["used"] != 0.25 {
			t.Fatalf("applying defaults reset %s usage", window)
		}
	}
	revision := usage["quotaRevision"].(int64)
	apply["revision"] = revision
	// A second application only renews the expiry. It must still advance the
	// optimistic quota revision instead of accepting a stale admin save.
	if w := client.request(t, "POST", "/admin/keys", apply); w.Code != http.StatusOK {
		t.Fatalf("renew: %d %s", w.Code, w.Body.String())
	}
	if w := client.request(t, "POST", "/admin/keys", apply); w.Code != http.StatusConflict {
		t.Fatal("expiry-only renewal accepted an old quota revision")
	}
}

func TestTierZeroDefaultsClearExpiryAndKeepManualCreationBehavior(t *testing.T) {
	g := testGateway(t)
	u := addUpstream(t, g, "zero-defaults", "", openAIBaseURL)
	installTier(t, g, limitedTier())
	client := newTierAdminTestClient(t, g)
	expiry := time.Now().Add(time.Hour)
	input := object{"name": "zero", "upstreamId": u.ID, "policy": keyPolicy{TierID: "basic", ExpiresAt: &expiry}, "useTierDefaults": true}
	w := client.request(t, "POST", "/admin/keys", input)
	var body struct{ Key accessKey }
	if w.Code != http.StatusCreated || json.Unmarshal(w.Body.Bytes(), &body) != nil || body.Key.Policy.ExpiresAt != nil || body.Key.Policy.usdLimited() {
		t.Fatal("zero defaults must mean permanent, unlimited key")
	}
	input["name"], input["useTierDefaults"] = "manual", false
	w = client.request(t, "POST", "/admin/keys", input)
	if w.Code != http.StatusCreated || json.Unmarshal(w.Body.Bytes(), &body) != nil || body.Key.Policy.ExpiresAt == nil || !body.Key.Policy.ExpiresAt.Equal(expiry) {
		t.Fatal("manual create behavior changed when defaults were not selected")
	}
}

func TestTierDefaultQuotaRejectsInvalidValuesAndTypes(t *testing.T) {
	g := testGateway(t)
	client := newTierAdminTestClient(t, g)
	base, err := json.Marshal(limitedTier())
	if err != nil {
		t.Fatal(err)
	}
	var tier map[string]json.RawMessage
	json.Unmarshal(base, &tier)
	for _, invalid := range []string{
		`null`, `[]`, `"30"`, `{"expiresInDays":-1}`, `{"expiresInDays":36501}`, `{"expiresInDays":1.5}`,
		`{"expiresInDays":"30"}`, `{"expiresInDays":true}`, `{"expiresInDays":null}`, `{"ExpiresInDays":null}`,
		`{"totalMicroUSD":-1}`, `{"fiveHourMicroUSD":-1}`, `{"dayMicroUSD":-1}`, `{"weekMicroUSD":-1}`,
		`{"totalMicroUSD":1000000000001}`, `{"totalMicroUSD":9223372036854775808}`,
		`{"fiveHourMicroUSD":1.2}`, `{"dayMicroUSD":"1"}`, `{"weekMicroUSD":null}`,
	} {
		t.Run(invalid, func(t *testing.T) {
			updated := maps.Clone(tier)
			updated["defaultQuota"] = json.RawMessage(invalid)
			w := client.request(t, "POST", "/admin/tiers", object{"tier": updated, "revision": g.appModels().Revision})
			if w.Code != http.StatusBadRequest {
				t.Fatalf("invalid default quota accepted: %d", w.Code)
			}
		})
	}
	for _, value := range []string{`null`, `"true"`, `1`, `{}`} {
		w := client.request(t, "POST", "/admin/keys", `{"name":"invalid","upstreamId":"unused","useTierDefaults":`+value+`}`)
		if w.Code != http.StatusBadRequest {
			t.Fatal("non-boolean checkbox accepted")
		}
	}
	if g.appModels().Revision != 0 {
		t.Fatal("invalid quota edits changed stored model settings")
	}
}
