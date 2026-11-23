package main

import (
	"encoding/json"
	"net/http"
	"net/netip"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestTierDeleteDefaultFallbackAndEmptyPersistence(t *testing.T) {
	g := testGateway(t)
	installTier(t, g, limitedTier())
	client := newTierAdminTestClient(t, g)
	enabled, rev := true, int64(0)
	ids := []string{"default"}
	published, err := g.updateLandingTiers(t.Context(), landingTierUpdate{Action: "sync", Enabled: &enabled, Revision: &rev, TierIDs: &ids}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	deleteTier := func(id string) {
		t.Helper()
		w := client.request(t, "POST", "/admin/tiers", object{"tier": object{"id": id}, "delete": true, "revision": g.appModels().Revision})
		if w.Code != http.StatusOK {
			t.Fatalf("delete %s: %d %s", id, w.Code, w.Body.String())
		}
	}
	deleteTier("default")
	if g.appModels().DefaultTierID != "basic" || len(g.appModels().Tiers) != 1 || g.appModels().Tiers[0].ID != "basic" {
		t.Fatal("deleting default changed a surviving tier ID or lost the fallback")
	}
	deleteTier("basic")
	if g.appModels().DefaultTierID != "" || g.appModels().Tiers == nil || len(g.appModels().Tiers) != 0 {
		t.Fatal("the last unbound tier was not removed")
	}
	if err := g.loadModels(t.Context()); err != nil || g.appModels().Tiers == nil || len(g.appModels().Tiers) != 0 || g.appModels().DefaultTierID != "" {
		t.Fatal("empty tier list resurrected the legacy default on reload")
	}
	var stored string
	g.store.db.QueryRow("SELECT value FROM gateway_settings WHERE name='models'").Scan(&stored)
	if !strings.Contains(stored, `"tiers":[]`) {
		t.Fatal("empty list was omitted from persistent JSON")
	}
	u := addUpstream(t, g, "empty", "", openAIBaseURL)
	if w := client.request(t, "POST", "/admin/keys", object{"name": "no tier", "upstreamId": u.ID}); w.Code != http.StatusBadRequest {
		t.Fatal("a key was issued without any tier")
	}
	first := limitedTier()
	first.ID, first.Name = "", "重新创建"
	w := client.request(t, "POST", "/admin/tiers", object{"tier": first, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK || len(g.appModels().Tiers) != 1 || g.appModels().DefaultTierID == "" || g.appModels().DefaultTierID == "default" {
		t.Fatal("could not create the first tier with its own stable ID")
	}
	w = client.request(t, "POST", "/admin/keys", object{"name": "fallback", "upstreamId": u.ID})
	var created struct{ Key accessKey }
	if w.Code != http.StatusCreated || json.Unmarshal(w.Body.Bytes(), &created) != nil || created.Key.Policy.TierID != g.appModels().DefaultTierID {
		t.Fatal("new key did not use the current defaultTierId")
	}
	snapshot, err := g.landingTiers(t.Context())
	if err != nil || snapshot.Revision != published.Revision || snapshot.Tiers[0].ID != "default" {
		t.Fatal("tier deletion altered a separately published snapshot")
	}
}

func TestTierLegacyNilAndSortedDefault(t *testing.T) {
	g := testGateway(t)
	legacy := modelDefaults(g.cfg)
	legacy.Tiers = nil
	if err := writeModels(t.Context(), g.store.db, &legacy); err != nil {
		t.Fatal(err)
	}
	if err := g.loadModels(t.Context()); err != nil || g.appModels().DefaultTierID != "default" || len(g.appModels().Tiers) != 1 {
		t.Fatal("legacy nil tiers did not initialize a default")
	}
	low, high := limitedTier(), fullTier()
	low.ID, high.ID = "low-original", "high-original"
	proposed := *g.appModels()
	proposed.Tiers = []featureTier{high, low}
	if _, err := g.saveModels(t.Context(), proposed); err != nil {
		t.Fatal(err)
	}
	if g.appModels().DefaultTierID != low.ID || g.appModels().Tiers[0].ID != low.ID || g.appModels().Tiers[1].ID != high.ID {
		t.Fatal("default fallback did not use permission order while preserving IDs")
	}
	u := addUpstream(t, g, "legacy-blank", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "implicit", Upstream: u.ID})
	profile, err := g.modelsForKey(k)
	if err != nil || profile.Tier.ID != low.ID {
		t.Fatal("legacy empty key tier did not resolve to the current default")
	}
	proposed = *g.appModels()
	proposed.Tiers = []featureTier{high}
	if _, err := g.saveModels(t.Context(), proposed); err == nil {
		t.Fatal("whole-model save bypassed explicit migration of an implicit binding")
	}
}

func TestTierDeletionMigratesKeysWithoutChangingPolicyOrUsage(t *testing.T) {
	g := testGateway(t)
	replacement := limitedTier()
	replacement.DefaultQuota = tierDefaultQuota{ExpiresInDays: 1, Total: 1}
	installTier(t, g, replacement)
	u := addUpstream(t, g, "migration", "", openAIBaseURL)
	expiry := time.Now().Add(30 * 24 * time.Hour)
	policy := keyPolicy{IPs: []string{"127.0.0.1/32"}, ExpiresAt: &expiry, Total: 9_000_000, H5: 3_000_000, D1: 5_000_000, D7: 8_000_000}
	keys := []accessKey{}
	for _, id := range []string{"", "default", "default"} {
		p := policy
		p.TierID = id
		k, _ := issue(t, g, accessKey{Name: "bound", Upstream: u.ID, Policy: p})
		request, err := g.store.reserve(t.Context(), route{key: k, upstream: u, clientIP: netip.MustParseAddr("127.0.0.1")}, time.Now(), 1)
		if err != nil {
			t.Fatal(err)
		}
		cost := int64(100_000)
		if err := g.store.finish(t.Context(), request, 200, nil, &cost); err != nil {
			t.Fatal(err)
		}
		keys = append(keys, k)
	}
	keys[2].Enabled = false
	if _, err := g.store.changeKey(t.Context(), keys[2].ID, &keys[2], "", nil, time.Now()); err != nil {
		t.Fatal(err)
	}
	unaffected, _ := issue(t, g, accessKey{Name: "unchanged", Upstream: u.ID, Policy: keyPolicy{TierID: replacement.ID}})
	changes, models := map[string]int64{}, 0
	g.onQuotaChange = func(id string, revision int64) { changes[id] = revision }
	g.onModelsChange = func(int64) { models++ }
	client := newTierAdminTestClient(t, g)
	for _, invalid := range []string{"", "default", "missing"} {
		w := client.request(t, "POST", "/admin/tiers", object{"tier": object{"id": "default"}, "delete": true,
			"replacementTierId": invalid, "revision": g.appModels().Revision})
		if w.Code != http.StatusBadRequest {
			t.Fatalf("invalid bound deletion accepted: %d", w.Code)
		}
	}
	w := client.request(t, "POST", "/admin/tiers", object{"tier": object{"id": "default"}, "delete": true,
		"replacementTierId": replacement.ID, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK || len(changes) != 3 || models != 1 {
		t.Fatalf("migration or notifications failed: %d %s", w.Code, w.Body.String())
	}
	for _, original := range keys {
		updated := readTierKey(t, g, original.ID)
		if updated.Policy.TierID != replacement.ID || updated.Enabled != original.Enabled || updated.Name != original.Name || updated.Upstream != original.Upstream {
			t.Fatal("migration lost a key or changed its non-tier settings")
		}
		updated.Policy.TierID = original.Policy.TierID
		if updated.Policy.encode() != original.Policy.encode() {
			t.Fatal("migration changed fee limits, expiry or IP restrictions")
		}
		usage, err := g.store.usage(t.Context(), readTierKey(t, g, original.ID), time.Now())
		if err != nil || changes[original.ID] == 0 {
			t.Fatal("migration omitted the quota revision")
		}
		for _, window := range []string{"total", "5h", "1d", "7d"} {
			if asObject(asObject(usage["usdBudget"])[window])["used"] != 0.1 {
				t.Fatalf("migration reset %s settled cost", window)
			}
		}
		if events := usage["quotaEvents"].([]quotaEventView); events[0].Kind != "tier_changed" {
			t.Fatal("migration was not recorded as a tier change")
		}
	}
	if readTierKey(t, g, unaffected.ID).Policy.TierID != replacement.ID || changes[unaffected.ID] != 0 {
		t.Fatal("an unrelated key was migrated")
	}
	if w := client.request(t, "POST", "/admin/tiers", object{"tier": object{"id": replacement.ID}, "delete": true, "revision": g.appModels().Revision}); w.Code != http.StatusBadRequest {
		t.Fatal("the last tier was deleted while keys remained bound")
	}
}

func TestTierDeleteTransactionRollbackAndConcurrentRevision(t *testing.T) {
	g := testGateway(t)
	installTier(t, g, limitedTier())
	u := addUpstream(t, g, "rollback", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "implicit", Upstream: u.ID})
	if _, err := g.store.db.Exec(`CREATE TRIGGER reject_tier_delete BEFORE UPDATE ON gateway_settings
		WHEN NEW.name='models' BEGIN SELECT RAISE(ABORT,'test failure'); END`); err != nil {
		t.Fatal(err)
	}
	revision := g.appModels().Revision
	if _, err := g.deleteTier(t.Context(), "default", "basic", revision); err == nil {
		t.Fatal("injected model write failure was ignored")
	}
	var events int
	g.store.db.QueryRow("SELECT COUNT(*) FROM quota_events WHERE key_id=? AND kind='tier_changed'", k.ID).Scan(&events)
	if readTierKey(t, g, k.ID).Policy.TierID != "" || events != 0 || g.appModels().Revision != revision || len(g.appModels().Tiers) != 2 {
		t.Fatal("failed deletion partially committed a key, event or model change")
	}
	g.store.db.Exec("DROP TRIGGER reject_tier_delete")
	client := newTierAdminTestClient(t, g)
	var group sync.WaitGroup
	statuses := make(chan int, 2)
	for range 2 {
		group.Go(func() {
			statuses <- client.request(t, "POST", "/admin/tiers", object{"tier": object{"id": "default"}, "delete": true,
				"replacementTierId": "basic", "revision": revision}).Code
		})
	}
	group.Wait()
	close(statuses)
	got := []int{}
	for code := range statuses {
		got = append(got, code)
	}
	slices.Sort(got)
	if !slices.Equal(got, []int{http.StatusOK, http.StatusConflict}) || readTierKey(t, g, k.ID).Policy.TierID != "basic" {
		t.Fatal("concurrent deletion did not serialize revisions and migration")
	}
}
