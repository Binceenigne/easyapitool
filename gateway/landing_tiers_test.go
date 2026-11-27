package main

import (
	"context"
	"encoding/json"
	"errors"
	"maps"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

func landingPublicTestHandler(t *testing.T, g *gateway) http.Handler {
	t.Helper()
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	t.Cleanup(func() { cancel(); m.workers.Wait() })
	mux := http.NewServeMux()
	m.registerRoutes(mux)
	return mux
}

func landingPublicTestRequest(handler http.Handler) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest("GET", "/api/v1/app/tiers", nil))
	return w
}

func landingAdminSnapshot(t *testing.T, w *httptest.ResponseRecorder) landingTierSnapshot {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("landing update: %d %s", w.Code, w.Body.String())
	}
	var result landingTierSnapshot
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestLandingTierPublicationIsAnIndependentPrivateSnapshot(t *testing.T) {
	g := testGateway(t)
	tier := limitedTier()
	tier.DefaultQuota = tierDefaultQuota{ExpiresInDays: 30, Total: 8_000_000, H5: 1_000_000}
	installTier(t, g, tier)
	u := addUpstream(t, g, "private-account", "private-pool", openAIBaseURL)
	_, secret := issue(t, g, accessKey{Name: "private-key-name", Upstream: u.ID})
	client := newTierAdminTestClient(t, g)
	public := landingPublicTestHandler(t, g)
	initial := landingPublicTestRequest(public)
	if initial.Code != http.StatusOK || initial.Body.String() != "{\"enabled\":false,\"publishedAt\":null,\"revision\":0,\"tiers\":[]}\n" {
		t.Fatalf("initial tiers must be hidden and empty: %d %s", initial.Code, initial.Body.String())
	}
	unauthenticated := tierAdminTestClient{handler: client.handler}
	if unauthenticated.request(t, "POST", "/admin/landing-tiers", object{}).Code != http.StatusUnauthorized {
		t.Fatal("publication endpoint is not protected by the PIN session")
	}
	if w := client.request(t, "POST", "/admin/landing-tiers", object{"action": "visibility", "enabled": true, "revision": 0}); w.Code != http.StatusBadRequest {
		t.Fatal("enabled publication without a snapshot")
	}
	sourceRevision := g.appModels().Revision
	published := landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "sync", "enabled": true, "tierIds": []string{"default", "basic"}, "revision": 0,
	}))
	if published.Revision != 1 || published.SourceRevision != sourceRevision || published.PublishedAt == nil ||
		!slices.Equal(published.TierIDs, []string{"basic", "default"}) || published.Tiers[0].DefaultQuota != tier.DefaultQuota {
		t.Fatal("publication did not capture selected tiers in permission order")
	}
	visible := landingPublicTestRequest(public)
	if visible.Code != http.StatusOK || visible.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("public snapshot unavailable or might serve a stale visibility state")
	}
	var publicData object
	if err := json.Unmarshal(visible.Body.Bytes(), &publicData); err != nil || len(publicData) != 4 {
		t.Fatal("unexpected public response envelope")
	}
	for _, field := range []string{"enabled", "revision", "publishedAt", "tiers"} {
		if _, ok := publicData[field]; !ok {
			t.Fatalf("missing public field %s", field)
		}
	}
	for _, private := range []string{secret, u.Secret, g.cfg.token, "private-account", "private-pool", "private-key-name", "bindingCount", "upstreams", "sourceRevision", "tierIds"} {
		if strings.Contains(visible.Body.String(), private) {
			t.Fatalf("public snapshot leaked administration data %q", private)
		}
	}
	for _, item := range publicData["tiers"].([]any) {
		fields := asObject(item)
		if len(fields) != 22 || fields["defaultQuota"] == nil || fields["rpmLimit"] == nil || fields["visualSkills"] != true {
			t.Fatal("public tier fields do not match the explicit safe whitelist")
		}
	}

	settings := *g.appModels()
	settings.Tiers = slices.Clone(settings.Tiers)
	for i := range settings.Tiers {
		if settings.Tiers[i].ID == "basic" {
			settings.Tiers[i].Name = "未发布的新名字"
			settings.Tiers[i].DefaultQuota.Total = 99_000_000
		}
	}
	if _, err := g.saveModels(t.Context(), settings); err != nil {
		t.Fatal(err)
	}
	if got := landingPublicTestRequest(public).Body.String(); got != visible.Body.String() {
		t.Fatal("saving editable models silently republished the landing snapshot")
	}
	settings = *g.appModels()
	settings.Tiers = slices.DeleteFunc(slices.Clone(settings.Tiers), func(tier featureTier) bool { return tier.ID == "basic" })
	if _, err := g.saveModels(t.Context(), settings); err != nil {
		t.Fatal(err)
	}
	if got := landingPublicTestRequest(public).Body.String(); got != visible.Body.String() {
		t.Fatal("deleting an unbound source tier changed an already published snapshot")
	}

	hidden := landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "visibility", "enabled": false, "tierIds": []string{"default"}, "revision": published.Revision,
	}))
	if hidden.Revision != 2 || hidden.SourceRevision != published.SourceRevision || !hidden.PublishedAt.Equal(*published.PublishedAt) ||
		!slices.Equal(hidden.TierIDs, published.TierIDs) || hidden.Tiers[0].Name != tier.Name {
		t.Fatal("visibility changed the snapshot rather than just the display switch")
	}
	hiddenPublic := landingPublicTestRequest(public)
	json.Unmarshal(hiddenPublic.Body.Bytes(), &publicData)
	if publicData["enabled"] != false || len(publicData["tiers"].([]any)) != 0 {
		t.Fatal("hidden publication still exposes its tier list")
	}
	reopened := landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "visibility", "enabled": true, "revision": hidden.Revision,
	}))
	if reopened.Tiers[0].ID != "basic" || reopened.SourceRevision != published.SourceRevision {
		t.Fatal("visibility refresh unexpectedly replaced deleted source tiers")
	}
	if w := client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "sync", "enabled": true, "tierIds": []string{"basic"}, "revision": reopened.Revision,
	}); w.Code != http.StatusBadRequest {
		t.Fatal("sync accepted a tier that has been deleted")
	}
	resynced := landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "sync", "enabled": true, "tierIds": []string{"default"}, "revision": reopened.Revision,
	}))
	if len(resynced.Tiers) != 1 || resynced.Tiers[0].ID != "default" || resynced.SourceRevision != g.appModels().Revision {
		t.Fatal("explicit synchronization did not replace the snapshot")
	}
	state := client.request(t, "GET", "/admin/state", nil)
	var adminData object
	if state.Code != http.StatusOK || json.Unmarshal(state.Body.Bytes(), &adminData) != nil || asObject(adminData["landingTiers"])["revision"] != float64(resynced.Revision) {
		t.Fatal("admin state omitted the saved landing settings")
	}
}

func TestLandingTierRevisionsTypesAndRestart(t *testing.T) {
	g := testGateway(t)
	tier := limitedTier()
	tier.DefaultQuota = tierDefaultQuota{ExpiresInDays: 36500, Total: 1_000_000_000_000}
	installTier(t, g, tier)
	client := newTierAdminTestClient(t, g)
	for _, input := range []string{
		`{}`, `{"action":"other","enabled":false,"revision":0}`,
		`{"action":"sync","enabled":false,"revision":0}`,
		`{"action":"sync","enabled":true,"tierIds":[],"revision":0}`,
		`{"action":"sync","enabled":true,"tierIds":["basic","basic"],"revision":0}`,
		`{"action":"sync","enabled":true,"tierIds":["missing"],"revision":0}`,
		`{"action":"visibility","enabled":null,"revision":0}`,
		`{"action":"visibility","enabled":"false","revision":0}`,
		`{"action":"visibility","enabled":false,"revision":-1}`,
		`{"action":"visibility","enabled":false,"revision":null}`,
		`{"action":"visibility","enabled":false,"revision":1.5}`,
		`{"action":"visibility","enabled":false,"revision":9223372036854775808}`,
		`{"action":"sync","enabled":false,"tierIds":null,"revision":0}`,
		`{"action":"sync","enabled":false,"tierIds":[1],"revision":0}`,
	} {
		if w := client.request(t, "POST", "/admin/landing-tiers", input); w.Code != http.StatusBadRequest {
			t.Fatalf("invalid publication accepted: %d %s", w.Code, input)
		}
	}
	saved := landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "sync", "enabled": false, "tierIds": []string{"basic"}, "revision": 0,
	}))
	if w := client.request(t, "POST", "/admin/landing-tiers", object{"action": "visibility", "enabled": true, "revision": 0}); w.Code != http.StatusConflict {
		t.Fatal("stale publication revision was accepted")
	}
	var sequence int
	var databaseName, databasePath string
	if err := g.store.db.QueryRow("PRAGMA database_list").Scan(&sequence, &databaseName, &databasePath); err != nil {
		t.Fatal(err)
	}
	if err := g.store.db.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := openStore(databasePath, g.cfg.token)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.db.Close()
	restarted := &gateway{store: reopened, cfg: g.cfg}
	if err := restarted.loadModels(t.Context()); err != nil {
		t.Fatal(err)
	}
	loaded, err := restarted.landingTiers(t.Context())
	if err != nil || loaded.Enabled || loaded.Revision != saved.Revision || loaded.SourceRevision != saved.SourceRevision ||
		!loaded.PublishedAt.Equal(*saved.PublishedAt) || loaded.Tiers[0].DefaultQuota != tier.DefaultQuota {
		t.Fatal("publication snapshot or default quota did not survive a database reopen")
	}
}

func TestLandingTierConcurrentSavesAndModelSnapshots(t *testing.T) {
	g := testGateway(t)
	installTier(t, g, limitedTier())
	enabled := true
	ids := []string{"basic"}
	revision := int64(0)
	input := landingTierUpdate{Action: "sync", Enabled: &enabled, TierIDs: &ids, Revision: &revision}
	var group sync.WaitGroup
	results := make(chan error, 2)
	for range 2 {
		group.Go(func() {
			_, err := g.updateLandingTiers(t.Context(), input, time.Now())
			results <- err
		})
	}
	group.Wait()
	close(results)
	success, conflicts := 0, 0
	for err := range results {
		if err == nil {
			success++
		} else if errors.Is(err, errLandingChanged) {
			conflicts++
		} else {
			t.Fatal(err)
		}
	}
	if success != 1 || conflicts != 1 {
		t.Fatal("same publication revision committed more than once")
	}
	current := g.appModels()
	proposed := *current
	proposed.Tiers = slices.Clone(current.Tiers)
	for i := range proposed.Tiers {
		if proposed.Tiers[i].ID == "basic" {
			proposed.Tiers[i].Name = "并发保存后的名字"
			proposed.Tiers[i].ImageModels = []string{"gpt-image-2.6-sunburst"}
		}
	}
	proposed.Reasoning = maps.Clone(proposed.Reasoning)
	revision = 1
	var snapshot landingTierSnapshot
	var publishErr, modelsErr error
	group.Go(func() { snapshot, publishErr = g.updateLandingTiers(t.Context(), input, time.Now()) })
	group.Go(func() { _, modelsErr = g.saveModels(t.Context(), proposed) })
	group.Wait()
	if publishErr != nil || modelsErr != nil {
		t.Fatalf("concurrent save failed: %v %v", publishErr, modelsErr)
	}
	switch snapshot.SourceRevision {
	case current.Revision:
		if snapshot.Tiers[0].Name != "基础" || snapshot.Tiers[0].ImageModels[0] != "gpt-image-2.6-flare" {
			t.Fatal("snapshot mixed old and new model revisions")
		}
	case current.Revision + 1:
		if snapshot.Tiers[0].Name != "并发保存后的名字" || snapshot.Tiers[0].ImageModels[0] != "gpt-image-2.6-sunburst" {
			t.Fatal("snapshot mixed old and new model revisions")
		}
	default:
		t.Fatal("snapshot source revision does not match either serialized model save")
	}
}
