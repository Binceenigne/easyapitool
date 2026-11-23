package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"slices"
	"strings"
	"testing"
)

func tierIDs(tiers []featureTier) []string {
	ids := make([]string, 0, len(tiers))
	for _, tier := range tiers {
		ids = append(ids, tier.ID)
	}
	return ids
}

func orderTestGateway(t *testing.T) (*gateway, tierAdminTestClient) {
	t.Helper()
	g := testGateway(t)
	low, high := limitedTier(), fullTier()
	low.ID, high.ID = "low", "high"
	p := *g.appModels()
	p.Tiers = []featureTier{high, low}
	if _, err := g.saveModels(t.Context(), p); err != nil {
		t.Fatal(err)
	}
	return g, newTierAdminTestClient(t, g)
}

func TestManualTierOrderPersistsWithoutChangingDefaultsOrBindings(t *testing.T) {
	g, client := orderTestGateway(t)
	u := addUpstream(t, g, "order-test", "", openAIBaseURL)
	key, _ := issue(t, g, accessKey{Name: "existing", Upstream: u.ID})
	before := readTierKey(t, g, key.ID)
	var response modelSettings
	w := client.request(t, "POST", "/admin/tiers/order", object{"ids": []string{"high", "low"}, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &response) != nil {
		t.Fatalf("manual order failed: %d %s", w.Code, w.Body.String())
	}
	if !slices.Equal(tierIDs(response.Tiers), []string{"high", "low"}) || !slices.Equal(response.TierOrder, []string{"high", "low"}) || response.DefaultTierID != "low" {
		t.Fatal("manual display order changed the default tier or was not applied")
	}
	if !reflect.DeepEqual(readTierKey(t, g, key.ID), before) {
		t.Fatal("display reorder changed an issued key")
	}
	if err := g.loadModels(t.Context()); err != nil || !slices.Equal(tierIDs(g.appModels().Tiers), []string{"high", "low"}) {
		t.Fatal("saved order lost after model reload", err)
	}
	// Existing model forms omit the newly added field; their saves must retain it.
	p := *g.appModels()
	p.TierOrder = nil
	p.ImageModels = slices.Clone(p.ImageModels)
	slices.Reverse(p.ImageModels)
	defaultImage := p.DefaultImage
	if _, err := g.saveModels(t.Context(), p); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(tierIDs(g.appModels().Tiers), []string{"high", "low"}) || !slices.Equal(g.appModels().ImageModels, p.ImageModels) || g.appModels().DefaultImage != defaultImage {
		t.Fatal("saving model order changed tier order or default image")
	}
	if err := g.loadModels(t.Context()); err != nil || !slices.Equal(g.appModels().ImageModels, p.ImageModels) {
		t.Fatal("image model order did not persist", err)
	}
	w = client.request(t, "POST", "/admin/tiers/order", object{"automatic": true, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK || len(g.appModels().TierOrder) != 0 || !slices.Equal(tierIDs(g.appModels().Tiers), []string{"low", "high"}) {
		t.Fatalf("restore automatic order failed: %d %s", w.Code, w.Body.String())
	}
}

func TestTierReorderRejectsStaleMissingUnknownAndDuplicateIDs(t *testing.T) {
	g, client := orderTestGateway(t)
	unauthenticated := tierAdminTestClient{handler: client.handler}
	if w := unauthenticated.request(t, "POST", "/admin/tiers/order", object{}); w.Code != http.StatusUnauthorized {
		t.Fatal("reorder endpoint must require a PIN session")
	}
	for _, ids := range [][]string{{"low"}, {"low", "low"}, {"missing", "high"}, {"", "high"}} {
		revision := g.appModels().Revision
		w := client.request(t, "POST", "/admin/tiers/order", object{"ids": ids, "revision": revision})
		if w.Code != http.StatusBadRequest || g.appModels().Revision != revision || len(g.appModels().TierOrder) != 0 {
			t.Fatalf("invalid order mutated state: %v %d", ids, w.Code)
		}
	}
	revision := g.appModels().Revision
	w := client.request(t, "POST", "/admin/tiers/order", object{"ids": []string{"high", "low"}, "revision": revision})
	if w.Code != http.StatusOK {
		t.Fatal(w.Code)
	}
	w = client.request(t, "POST", "/admin/tiers/order", object{"automatic": true, "revision": revision})
	if w.Code != http.StatusConflict || !slices.Equal(g.appModels().TierOrder, []string{"high", "low"}) {
		t.Fatal("stale reorder replaced the saved order")
	}
}

func TestManualTierOrderSurvivesEditAppendDeleteAndSnapshotSync(t *testing.T) {
	g, client := orderTestGateway(t)
	w := client.request(t, "POST", "/admin/tiers/order", object{"ids": []string{"high", "low"}, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK {
		t.Fatal(w.Code)
	}
	snapshot := landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "sync", "enabled": true, "tierIds": []string{"low", "high"}, "revision": 0,
	}))
	if !slices.Equal(snapshot.TierIDs, []string{"high", "low"}) {
		t.Fatal("landing publication ignored the manually saved sequence")
	}
	p := *g.appModels()
	p.Tiers = slices.Clone(p.Tiers)
	p.Tiers[0].Name = "renamed high"
	newTier := limitedTier()
	newTier.ID = "new-low"
	p.Tiers = append(p.Tiers, newTier)
	if _, err := g.saveModels(t.Context(), p); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(tierIDs(g.appModels().Tiers), []string{"high", "low", "new-low"}) {
		t.Fatal("editing or appending a tier disrupted manual order")
	}
	if _, err := g.deleteTier(t.Context(), "low", "", g.appModels().Revision); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(g.appModels().TierOrder, []string{"high", "new-low"}) {
		t.Fatal("deleted tier remained in the saved order")
	}
	stored, err := g.landingTiers(t.Context())
	if err != nil || !reflect.DeepEqual(stored, snapshot) {
		t.Fatal("workspace edits changed an already published snapshot", err)
	}
	w = client.request(t, "POST", "/admin/tiers/order", object{"automatic": true, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK {
		t.Fatal(w.Code)
	}
	snapshot = landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "sync", "enabled": true, "tierIds": []string{"high", "new-low"}, "revision": stored.Revision,
	}))
	if !slices.Equal(snapshot.TierIDs, []string{"new-low", "high"}) {
		t.Fatal("publication did not use the restored permission order")
	}
}

func TestTierOrderKeepsImplicitDefaultWhenNamesAndScoresTie(t *testing.T) {
	g := testGateway(t)
	first, second := limitedTier(), limitedTier()
	first.ID, second.ID = "first", "second"
	first.RPMLimit, second.RPMLimit = 3, 30
	second.ImageModels = []string{"gpt-image-2.6-sunburst"}
	p := *g.appModels()
	p.Tiers = []featureTier{first, second}
	if _, err := g.saveModels(t.Context(), p); err != nil {
		t.Fatal(err)
	}
	client := newTierAdminTestClient(t, g)
	w := client.request(t, "POST", "/admin/tiers/order", object{"ids": []string{second.ID, first.ID}, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK || g.appModels().DefaultTierID != first.ID {
		t.Fatalf("display reorder changed a tied implicit default: %d %s", w.Code, w.Body.String())
	}
	// Existing model forms omit the default field as well as tierOrder.
	p = *g.appModels()
	p.DefaultTierID, p.TierOrder = "", nil
	if _, err := g.saveModels(t.Context(), p); err != nil {
		t.Fatal(err)
	}
	if err := g.loadModels(t.Context()); err != nil {
		t.Fatal(err)
	}
	profile, err := g.modelsForKey(accessKey{})
	if err != nil || profile.Tier.ID != first.ID || !slices.Equal(profile.ImageModels, first.ImageModels) {
		t.Fatal("implicit key model permissions changed after reorder, save or reload", err)
	}
	tx, err := g.store.db.BeginTx(t.Context(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	tier, err := tierForKeyTx(t.Context(), tx, accessKey{})
	if err != nil || tier.ID != first.ID || tier.RPMLimit != first.RPMLimit {
		t.Fatal("admission ledger resolved a different implicit default", err)
	}
	if err := tx.Rollback(); err != nil {
		t.Fatal(err)
	}
	// A real permission change still recomputes the default by permission rank.
	p = *g.appModels()
	p.Tiers = slices.Clone(p.Tiers)
	p.Tiers[0].Formats = []string{"png"}
	if _, err := g.saveModels(t.Context(), p); err != nil || g.appModels().DefaultTierID != second.ID {
		t.Fatal("remembering a tie froze the default after a permission change", err)
	}
}

func TestTierOrderAcceptsAll64MaximumLengthIDs(t *testing.T) {
	g := testGateway(t)
	p := *g.appModels()
	p.Tiers = make([]featureTier, 64)
	ids := make([]string, len(p.Tiers))
	for i := range p.Tiers {
		tier := limitedTier()
		tier.ID = strings.Repeat("x", 125) + fmt.Sprintf("%03d", i)
		tier.Name = fmt.Sprintf("tier %02d", i)
		p.Tiers[i], ids[i] = tier, tier.ID
	}
	if _, err := g.saveModels(t.Context(), p); err != nil {
		t.Fatal(err)
	}
	slices.Reverse(ids)
	client := newTierAdminTestClient(t, g)
	w := client.request(t, "POST", "/admin/tiers/order", object{"ids": ids, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK || !slices.Equal(g.appModels().TierOrder, ids) {
		t.Fatalf("valid maximum-size order was rejected: %d %s", w.Code, w.Body.String())
	}
}
