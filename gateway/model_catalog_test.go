package main

import (
	"context"
	"encoding/json"
	"slices"
	"testing"
)

func TestTierCatalogExplainsLocksWithoutGrantingPermissions(t *testing.T) {
	g := testGateway(t)
	installTier(t, g, limitedTier())
	key := accessKey{ID: "catalog-test-key", Policy: keyPolicy{TierID: "basic"}}
	profile, err := g.modelsForKey(key)
	if err != nil {
		t.Fatal(err)
	}
	if profile.Catalog == nil || profile.Tier == nil {
		t.Fatal("missing catalog or actual tier")
	}
	model := profile.ImageModels[0]
	if !slices.Equal(profile.ImageFeatures[model].Quality, []string{"medium"}) || len(profile.Catalog.ImageFeatures[model].Quality) <= 1 {
		t.Fatal("catalog must retain native quality while effective profile remains restricted")
	}
	blocked := profile.Catalog.Reasoning["terra"].Model
	if blocked == "" || profile.Reasoning["terra"].Model != "" {
		t.Fatal("catalog must retain the locked model without granting its slot")
	}
	if err := g.checkReasoningTier(key, blocked, "high", "advanced", false); err == nil {
		t.Fatal("catalog must not bypass tier authorization")
	}
	profile.Catalog.ImageFeatures[model].Quality[0] = "changed"
	profile.Catalog.ImageModels[0] = "changed"
	if g.appModels().ImageModels[0] == "changed" || g.appModels().ImageFeatures[model].Quality[0] == "changed" {
		t.Fatal("catalog aliases the authoritative configuration")
	}
}

func TestModelCatalogIsReadOnlyAndDoesNotPersist(t *testing.T) {
	g := testGateway(t)
	profile := *g.appModels()
	profile.Catalog = &modelCatalog{ImageModels: []string{"forged-model"}}
	saved, err := g.saveModels(context.Background(), profile)
	if err != nil {
		t.Fatal(err)
	}
	if saved.Catalog != nil {
		t.Fatal("catalog must be derived, not accepted as configuration")
	}
	var data []byte
	if err := g.store.db.QueryRow("SELECT value FROM gateway_settings WHERE name='models'").Scan(&data); err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		t.Fatal(err)
	}
	if _, exists := fields["catalog"]; exists {
		t.Fatal("derived catalog must not be stored")
	}
}

func TestTierCannotOmitNativeComplexityToBypassAllowedDepth(t *testing.T) {
	g := testGateway(t)
	tier := limitedTier()
	tier.Formats = []string{"png", "gif", "svg"}
	installTier(t, g, tier)
	key := accessKey{ID: "complexity-test-key", Policy: keyPolicy{TierID: tier.ID}}
	options := imageOptions{model: "gpt-image-2.6-flare", format: "png", quality: "medium", background: "opaque", size: "1536x1024", n: 1}
	if !g.appModels().ImageFeatures[options.model].Complexity {
		t.Fatal("test requires a native complexity model")
	}
	if err := g.checkImageTier(key, options, 0); err == nil {
		t.Fatal("omitting complexity bypassed the low-only tier")
	}
	options.complexity = "low"
	if err := g.checkImageTier(key, options, 0); err != nil {
		t.Fatal(err)
	}
	options.complexity = "high"
	if err := g.checkImageTier(key, options, 0); err == nil {
		t.Fatal("high complexity bypassed the low-only tier")
	}
	profile := *g.appModels()
	profile.Tiers = slices.Clone(profile.Tiers)
	for i := range profile.Tiers {
		if profile.Tiers[i].ID == tier.ID {
			profile.Tiers[i].Complexities = []string{}
		}
	}
	if _, err := g.saveModels(t.Context(), profile); err != nil {
		t.Fatal(err)
	}
	options.complexity = ""
	if err := g.checkImageTier(key, options, 0); err == nil {
		t.Fatal("an empty allowlist granted native default complexity")
	}
	options.format = "svg"
	if err := g.checkImageTier(key, options, 0); err != nil {
		t.Fatalf("SVG must not require an image-model complexity: %v", err)
	}
}
