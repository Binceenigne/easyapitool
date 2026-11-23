package main

import (
	"encoding/json"
	"net/http"
	"reflect"
	"testing"
)

func TestTierPreviewUsesKeyPermissionsWithoutIssuingOrBilling(t *testing.T) {
	g := testGateway(t)
	tier := limitedTier()
	tier.RPMLimit = 12
	tier.DefaultQuota.Total = 20_000_000
	installTier(t, g, tier)
	client := newTierAdminTestClient(t, g)
	revision := g.appModels().Revision
	unauthenticated := tierAdminTestClient{handler: client.handler}
	if response := unauthenticated.request(t, http.MethodGet, "/admin/tiers/basic/preview", nil); response.Code != http.StatusUnauthorized {
		t.Fatalf("preview must require PIN session: %d", response.Code)
	}
	response := client.request(t, http.MethodGet, "/admin/tiers/basic/preview", nil)
	if response.Code != http.StatusOK {
		t.Fatalf("preview status: %d", response.Code)
	}
	var profile modelSettings
	if err := json.Unmarshal(response.Body.Bytes(), &profile); err != nil {
		t.Fatal(err)
	}
	expected, err := g.modelsForKey(accessKey{Policy: keyPolicy{TierID: tier.ID}})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(profile, *expected) || profile.Tier == nil || profile.Tier.Advanced || profile.Tier.Transparency || profile.Tier.RPMLimit != 12 {
		t.Fatal("preview does not match the real key profile")
	}
	var keys, requests int
	if err := g.store.db.QueryRow("SELECT COUNT(*) FROM access_keys").Scan(&keys); err != nil {
		t.Fatal(err)
	}
	if err := g.store.db.QueryRow("SELECT COUNT(*) FROM requests").Scan(&requests); err != nil {
		t.Fatal(err)
	}
	if keys != 0 || requests != 0 || g.appModels().Revision != revision {
		t.Fatal("preview changed credentials, billing, or configuration")
	}
	if response := client.request(t, http.MethodGet, "/admin/tiers/missing/preview", nil); response.Code != http.StatusNotFound {
		t.Fatalf("unknown tier must return 404: %d", response.Code)
	}
}
