package main

import (
	"encoding/json"
	"maps"
	"net/http"
	"reflect"
	"slices"
	"testing"
)

func tierOptionsSettings(t *testing.T) modelSettings {
	t.Helper()
	g := testGateway(t)
	settings := modelDefaults(g.cfg)
	settings.ImageModels = []string{"gpt-image-1.5", "gpt-image-2.6-flare"}
	settings.DefaultImage = settings.ImageModels[0]
	settings.SVG.Enabled = true
	settings.Reasoning["luna"] = modelPreset{Model: "gpt-5-pro", Effort: "high"}
	settings.ReasoningModes["flash"] = settings.Reasoning["luna"]
	settings.applyCapabilities()
	return settings
}

func TestTierOptionsFollowSelectedNativeModelsWithoutRewritingPolicy(t *testing.T) {
	settings := tierOptionsSettings(t)
	draft := fullTier()
	draft.ImageModels = []string{"gpt-image-1.5"}
	draft.ReasoningModels = []string{"gpt-5-pro"}
	draft.DefaultQuota = tierDefaultQuota{ExpiresInDays: 30, Total: 99, H5: 5, D1: 10, D7: 40}
	draft.RPMLimit = 17
	before, _ := json.Marshal(draft)
	settingsBefore, _ := json.Marshal(settings)
	options, effective := tierOptions(&settings, &draft)
	if !slices.Equal(options.ImageModels, settings.ImageModels) || !slices.Contains(options.ReasoningModels, "gpt-6.1-sol") {
		t.Fatal("model choices must remain global when a subset is selected")
	}
	if !slices.Equal(options.Qualities, []string{"low", "medium", "high"}) || len(options.Complexities) != 0 ||
		!slices.Equal(options.Formats, []string{"png", "jpeg", "webp"}) || options.MaxResolution != 1536 || options.MaxGIFFrames != 0 {
		t.Fatalf("fixed image model received unsupported controls: %+v", options)
	}
	if !options.Transparency || !options.ImageEditing || options.MaxReferences != 16 {
		t.Fatal("the 1.5 model must retain documented PNG/WebP transparency and image editing")
	}
	if !slices.Equal(options.ReasoningEfforts, []string{"high"}) || !slices.Equal(options.ReasoningModes, []string{"instant", "flash"}) {
		t.Fatalf("reasoning controls did not follow the selected model: %+v", options)
	}
	if effective.MaxResolution != 1536 || effective.MaxGIFFrames != 4 || effective.DefaultQuota != draft.DefaultQuota ||
		effective.RPMLimit != draft.RPMLimit || effective.Name != draft.Name || effective.ID != draft.ID {
		t.Fatal("effective tier did not preserve metadata and billing defaults")
	}
	if err := validateTier(effective); err != nil {
		t.Fatal("derived tier no longer meets the public snapshot schema", err)
	}
	after, _ := json.Marshal(draft)
	settingsAfter, _ := json.Marshal(settings)
	if string(before) != string(after) || string(settingsBefore) != string(settingsAfter) {
		t.Fatal("calculating options changed the draft or authoritative model settings")
	}
	// The union expands as soon as a capable image model is selected. A switch
	// allowlist currently being off must not remove its own native choices.
	draft.ImageModels = append(draft.ImageModels, "gpt-image-2.6-flare")
	draft.Formats, draft.Qualities, draft.ReasoningEfforts = []string{}, []string{}, []string{}
	draft.ImageEditing, draft.Transparency, draft.Advanced = false, false, false
	options, effective = tierOptions(&settings, &draft)
	if len(options.Qualities) != 5 || len(options.Complexities) != 3 || len(options.Formats) != 5 ||
		options.MaxResolution != 3840 || options.MaxGIFFrames != 0 || options.ImageEditing || options.Transparency || options.Advanced {
		t.Fatal("native allowlists or their format/effort dependencies did not follow the selected fields")
	}
	if len(effective.Formats) != 0 || effective.Transparency || effective.Advanced || effective.ImageEditing {
		t.Fatal("native availability implicitly enabled a disabled permission")
	}
	settings.SVG.Enabled = false
	options, _ = tierOptions(&settings, &draft)
	if slices.Contains(options.Formats, "svg") || !slices.Contains(options.Formats, "gif") {
		t.Fatal("global SVG switch did not restrict format choices independently of GIF")
	}
}

func TestTierOptionsFollowCrossFieldDependenciesAndRetainDirectInstant(t *testing.T) {
	settings := tierOptionsSettings(t)
	settings.ReasoningModes["instant"] = modelPreset{Model: "gpt-6.1-sol", Effort: "max"}
	draft := fullTier()
	draft.ImageModels = []string{"gpt-image-1.5"}
	draft.ReasoningModels = []string{"gpt-5-pro"}
	draft.ReasoningModes = []string{"flash"}
	draft.ReasoningEfforts = []string{"low"}
	draft.Formats = []string{"jpeg"}
	options, effective := tierOptions(&settings, &draft)
	if !slices.Equal(options.ReasoningModes, []string{"instant"}) || options.Advanced || options.WebSearch || options.PromptPolish || options.Transparency || !options.ImageEditing {
		t.Fatal("dependent controls did not reflect the selected efforts and JPEG format")
	}
	if !slices.Equal(effective.ReasoningModes, []string{"instant"}) || effective.Advanced || effective.Transparency {
		t.Fatal("effective policy did not preserve direct generation without enabling a forbidden Instant language model")
	}
	draft.ReasoningEfforts = []string{"high"}
	draft.Formats = []string{"webp"}
	draft.Advanced, draft.Transparency, draft.ImageEditing = false, false, false
	options, effective = tierOptions(&settings, &draft)
	if !slices.Equal(options.ReasoningModes, []string{"instant", "flash"}) || !options.Advanced || !options.WebSearch || !options.PromptPolish || !options.Transparency || !options.ImageEditing || options.MaxReferences != 16 {
		t.Fatal("an unchecked switch removed the available way to turn a supported feature on")
	}
	if effective.Transparency || effective.Advanced || effective.ImageEditing {
		t.Fatal("supported native abilities enabled an unchecked switch")
	}
	draft.ImageModels = []string{"gpt-image-2.6-flare"}
	draft.Formats = []string{"svg"}
	options, effective = tierOptions(&settings, &draft)
	if options.Transparency || options.ImageEditing || options.MaxGIFFrames != 0 || options.MaxReferences != 0 || effective.MaxGIFFrames != 4 {
		t.Fatal("SVG inherited raster transparency, editing or GIF frame controls")
	}
	draft.Formats = []string{"gif"}
	options, _ = tierOptions(&settings, &draft)
	if options.Transparency || !options.ImageEditing || options.MaxGIFFrames != 16 {
		t.Fatal("GIF dependencies did not preserve editing and animation while excluding transparent backgrounds")
	}
}

func TestTierOptionsEmptyModelsAndUnknownModelsDoNotInventAbilities(t *testing.T) {
	settings := tierOptionsSettings(t)
	draft := fullTier()
	draft.ImageModels, draft.ReasoningModels = []string{}, []string{}
	options, effective := tierOptions(&settings, &draft)
	if options.MaxResolution != 0 || options.MaxImages != 0 || options.MaxReferences != 0 || len(options.Formats) != 0 ||
		len(options.ReasoningEfforts) != 0 || options.Transparency || options.Advanced || effective.Transparency {
		t.Fatal("selecting no model granted native abilities")
	}
	if err := validateTier(effective); err != nil || len(effective.ImageModels) != 0 || len(effective.Formats) != 0 {
		t.Fatal("empty projected tier must remain schema-valid without granting image generation", err)
	}
	settings.ImageModels = []string{"undocumented-image"}
	draft.ImageModels = []string{"*"}
	options, effective = tierOptions(&settings, &draft)
	if !slices.Equal(effective.ImageModels, settings.ImageModels) || len(options.Qualities) != 0 || options.MaxResolution != 0 || len(options.Formats) != 0 {
		t.Fatal("unknown model abilities were guessed or its configured ID was discarded")
	}
}

func TestTierOptionsAPIIsProtectedRevisionedAndReadOnly(t *testing.T) {
	g := testGateway(t)
	settings := tierOptionsSettings(t)
	settings.Revision = g.appModels().Revision
	if _, err := g.saveModels(t.Context(), settings); err != nil {
		t.Fatal(err)
	}
	client := newTierAdminTestClient(t, g)
	unauthenticated := tierAdminTestClient{handler: client.handler}
	if w := unauthenticated.request(t, "POST", "/admin/tier-options", object{}); w.Code != http.StatusUnauthorized {
		t.Fatal("tier options must require a PIN session")
	}
	before, _ := json.Marshal(g.appModels())
	for _, body := range []string{`{}`, `{"tier":null,"revision":1}`, `{"tier":{},"revision":"1"}`, `{"tier":{"advanced":"yes"},"revision":1}`, `{"tier":{}}`} {
		if w := client.request(t, "POST", "/admin/tier-options", body); w.Code != http.StatusBadRequest {
			t.Fatalf("invalid request accepted: %s, %d", body, w.Code)
		}
	}
	if w := client.request(t, "POST", "/admin/tier-options", object{"tier": featureTier{}, "revision": -1}); w.Code != http.StatusBadRequest {
		t.Fatal("negative model revision accepted")
	}
	if w := client.request(t, "POST", "/admin/tier-options", object{"tier": featureTier{}, "revision": g.appModels().Revision + 1}); w.Code != http.StatusConflict {
		t.Fatal("stale model revision accepted")
	}
	draft := fullTier()
	draft.Name, draft.ID, draft.ImageModels = "", "", []string{"gpt-image-1.5"}
	w := client.request(t, "POST", "/admin/tier-options", object{"tier": draft, "revision": g.appModels().Revision})
	var result struct {
		Revision int64                 `json:"revision"`
		Options  tierCapabilityOptions `json:"options"`
		Tier     featureTier           `json:"tier"`
	}
	if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &result) != nil || result.Options.MaxResolution != 1536 || result.Tier.Name != "" {
		t.Fatalf("new unnamed draft cannot be previewed: %d %s", w.Code, w.Body.String())
	}
	after, _ := json.Marshal(g.appModels())
	if string(before) != string(after) {
		t.Fatal("read-only editor options modified policy")
	}
	state := client.request(t, "GET", "/admin/state", nil)
	var view struct {
		Models    modelSettings `json:"appModels"`
		Effective []featureTier `json:"effectiveTiers"`
	}
	if state.Code != http.StatusOK || json.Unmarshal(state.Body.Bytes(), &view) != nil ||
		!reflect.DeepEqual(view.Models.Tiers, g.appModels().Tiers) || !reflect.DeepEqual(view.Effective, effectiveTiers(g.appModels())) {
		t.Fatal("admin state did not keep raw policy alongside the derived effective tiers")
	}
}

func TestTierSavePreservesInheritanceAndBillingWhileNormalizingExplicitNativeChoices(t *testing.T) {
	g := testGateway(t)
	settings := tierOptionsSettings(t)
	settings.Revision = g.appModels().Revision
	if _, err := g.saveModels(t.Context(), settings); err != nil {
		t.Fatal(err)
	}
	client := newTierAdminTestClient(t, g)
	tier := fullTier()
	tier.ID, tier.Name = "native-fixed", "旧版图像模型"
	tier.ImageModels = []string{"gpt-image-1.5"}
	tier.DefaultQuota = tierDefaultQuota{ExpiresInDays: 45, Total: 90_000_000, H5: 2_000_000, D1: 5_000_000, D7: 20_000_000}
	tier.RPMLimit = 23
	w := client.request(t, "POST", "/admin/tiers", object{"tier": tier, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK {
		t.Fatalf("save inherited tier: %d %s", w.Code, w.Body.String())
	}
	saved, _ := g.appModels().tier(tier.ID)
	if !slices.Equal(saved.Qualities, []string{"*"}) || !slices.Equal(saved.Formats, []string{"*"}) || !slices.Equal(saved.Complexities, []string{"*"}) || saved.MaxResolution != 1536 || saved.DefaultQuota != tier.DefaultQuota || saved.RPMLimit != tier.RPMLimit {
		t.Fatal("save expanded inheritance or changed billing defaults")
	}
	saved.Qualities, saved.Formats, saved.Complexities = []string{"high", "max"}, []string{"png", "svg", "gif"}, []string{"high"}
	w = client.request(t, "POST", "/admin/tiers", object{"tier": saved, "revision": g.appModels().Revision})
	if w.Code != http.StatusOK {
		t.Fatalf("save explicit tier: %d %s", w.Code, w.Body.String())
	}
	saved, _ = g.appModels().tier(tier.ID)
	if !slices.Equal(saved.Qualities, []string{"high"}) || !slices.Equal(saved.Formats, []string{"png"}) || len(saved.Complexities) != 0 || !saved.Transparency {
		t.Fatal("explicit impossible values survived, or valid transparency was removed")
	}
}

func TestLandingSyncCapturesEffectiveTierWithoutEditingRawOrOldSnapshot(t *testing.T) {
	g := testGateway(t)
	settings := tierOptionsSettings(t)
	settings.Revision = g.appModels().Revision
	settings.Tiers = []featureTier{fullTier()}
	settings.Tiers[0].ImageModels = []string{"gpt-image-1.5"}
	if _, err := g.saveModels(t.Context(), settings); err != nil {
		t.Fatal(err)
	}
	client := newTierAdminTestClient(t, g)
	published := landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "sync", "enabled": true, "tierIds": []string{"default"}, "revision": 0,
	}))
	if published.Tiers[0].MaxResolution != 1536 || len(published.Tiers[0].Qualities) != 3 || slices.Contains(published.Tiers[0].Formats, "gif") || !published.Tiers[0].Transparency {
		t.Fatal("landing snapshot advertised capabilities its selected model does not provide")
	}
	if g.appModels().Tiers[0].MaxResolution != 3840 || !slices.Equal(g.appModels().Tiers[0].Formats, []string{"*"}) {
		t.Fatal("publishing migrated the stored raw policy")
	}
	updated := *g.appModels()
	updated.Tiers = slices.Clone(updated.Tiers)
	updated.Tiers[0].ImageModels = []string{"gpt-image-2.6-flare"}
	if _, err := g.saveModels(t.Context(), updated); err != nil {
		t.Fatal(err)
	}
	old, err := g.landingTiers(t.Context())
	if err != nil || !reflect.DeepEqual(old, published) {
		t.Fatal("an editable model change altered an existing published snapshot", err)
	}
	published = landingAdminSnapshot(t, client.request(t, "POST", "/admin/landing-tiers", object{
		"action": "sync", "enabled": true, "tierIds": []string{"default"}, "revision": old.Revision,
	}))
	if published.Tiers[0].MaxResolution != 3840 || len(published.Tiers[0].Qualities) != 5 || !slices.Contains(published.Tiers[0].Formats, "gif") {
		t.Fatal("resync did not capture the newly selected native model abilities")
	}
}

func TestNativeAndAdapterCannotUseWildcardToBypassModelCapabilities(t *testing.T) {
	g := testGateway(t)
	settings := tierOptionsSettings(t)
	settings.Revision = g.appModels().Revision
	if _, err := g.saveModels(t.Context(), settings); err != nil {
		t.Fatal(err)
	}
	key := accessKey{ID: "native-cap-test", Policy: keyPolicy{TierID: "default"}}
	base := object{"model": "gpt-image-1.5", "size": "1536x1024", "quality": "high", "output_format": "png", "background": "transparent"}
	for _, invalid := range []object{{"size": "2560x1696"}, {"size": "1024x768"}, {"quality": "max"}, {"quality": "xhigh"}, {"complexity": "high"}, {"output_format": "gif"}, {"output_format": "svg"}} {
		native := maps.Clone(base)
		maps.Copy(native, invalid)
		if err := g.checkNativeImage(key, native, 0, false); err == nil {
			t.Fatalf("native wildcard accepted unsupported model parameters: %v", invalid)
		}
		adapter := maps.Clone(native)
		adapter["imageModel"] = adapter["model"]
		delete(adapter, "model")
		if _, err := g.imageOptions(adapter, false, g.appModels()); err == nil {
			t.Fatalf("application adapter accepted unsupported model parameters: %v", invalid)
		}
	}
	for _, compatible := range []object{base, {"model": "gpt-image-1.5", "quality": "auto", "size": "auto"}, {"model": "gpt-image-1.5", "output_format": "webp", "background": "transparent"}} {
		if err := g.checkNativeImage(key, compatible, 0, false); err != nil {
			t.Fatalf("native valid/auto parameters rejected: %v, %v", compatible, err)
		}
	}
	if err := g.checkReasoningTier(key, "gpt-5-pro", "xhigh", "advanced", false); err == nil {
		t.Fatal("a wildcard tier granted an effort unsupported by the reasoning model")
	}
	if err := g.checkReasoningTier(key, "gpt-5-pro", "high", "advanced", false); err != nil {
		t.Fatal("supported reasoning effort rejected", err)
	}
}
