package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"
	"time"
)

func limitedTier() featureTier {
	t := fullTier()
	t.ID = "basic"
	t.Name = "基础"
	t.ImageModels = []string{"gpt-image-2.6-flare"}
	t.ReasoningModels = []string{"gpt-6-luna"}
	t.ReasoningModes = []string{"instant", "flash"}
	t.ReasoningEfforts = []string{"medium"}
	t.Qualities = []string{"medium"}
	t.Complexities = []string{"low"}
	t.Formats = []string{"png", "jpeg", "webp"}
	t.Advanced = false
	t.Transparency = false
	t.WebSearch = false
	t.PromptPolish = false
	t.ImageEditing = false
	t.MaxResolution = 1536
	t.MaxImages = 1
	t.MaxReferences = 0
	t.MaxGIFFrames = 4
	return t
}

func TestAdminTierAssetUsesManagedNamespace(t *testing.T) {
	handler := newAdmin(testGateway(t)).routes(nil)
	for _, tc := range []struct{ path, content string }{
		{"/", `src="/admin/assets/admin-tiers.js`},
		{"/", `src="/admin/assets/admin-tier-publish.js`},
		{"/admin/assets/admin-tiers.js", "window.prepareKeyTier"},
		{"/admin/assets/admin-tier-publish.js", "window.renderLandingTierSettings"},
	} {
		req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8771"+tc.path, nil)
		reply := httptest.NewRecorder()
		handler.ServeHTTP(reply, req)
		if reply.Code != http.StatusOK || !strings.Contains(reply.Body.String(), tc.content) {
			t.Fatalf("admin asset %s: status %d, content missing", tc.path, reply.Code)
		}
	}
}

func installTier(t *testing.T, g *gateway, tier featureTier) {
	t.Helper()
	profile := *g.appModels()
	profile.Tiers = append(slices.Clone(profile.Tiers), tier)
	if _, err := g.saveModels(t.Context(), profile); err != nil {
		t.Fatal(err)
	}
}

func TestTierAuthorizesBeforeForwardingAndPreservesBody(t *testing.T) {
	g := testGateway(t)
	installTier(t, g, limitedTier())
	var forwarded string
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, _ := io.ReadAll(r.Body)
		forwarded = string(data)
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"data":[]}`)
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "tiers", "", provider.URL+"/v1")
	_, secret := issue(t, g, accessKey{Name: "basic", Upstream: u.ID, Policy: keyPolicy{TierID: "basic"}})
	mux := http.NewServeMux()
	g.registerProxy(mux)
	base := object{"model": "gpt-image-2.6-flare", "quality": "medium", "size": "1536x1024", "background": "opaque", "output_format": "png", "complexity": "low", "n": 1, "prompt": strings.Repeat("保留提示词与参考数据", 3000)}
	for _, tc := range []struct {
		field string
		value any
	}{
		{"model", "gpt-image-2.6-sunburst"}, {"quality", "max"}, {"quality", "auto"}, {"size", "3520x2352"}, {"size", "auto"},
		{"background", "transparent"}, {"background", "auto"}, {"output_format", "svg"}, {"output_format", "gif"}, {"n", 2}, {"complexity", "high"},
	} {
		t.Run(tc.field+"="+fmt.Sprint(tc.value), func(t *testing.T) {
			data := maps.Clone(base)
			data[tc.field] = tc.value
			encoded, _ := json.Marshal(data)
			req := httptest.NewRequest("POST", "/v1/images/generations", bytes.NewReader(encoded))
			req.Header.Set("Authorization", "Bearer "+secret)
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			mux.ServeHTTP(w, req)
			if w.Code != 403 || forwarded != "" {
				t.Fatalf("restricted request reached upstream: %d", w.Code)
			}
		})
	}
	encoded, _ := json.Marshal(base)
	req := httptest.NewRequest("POST", "/v1/images/generations", bytes.NewReader(encoded))
	req.Header.Set("Authorization", "Bearer "+secret)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)
	if w.Code != 200 || forwarded != string(encoded) {
		t.Fatalf("allowed request changed: %d", w.Code)
	}
}

func TestTierProfileAndAdvancedDepthRestrictions(t *testing.T) {
	g := testGateway(t)
	installTier(t, g, limitedTier())
	k := accessKey{ID: "fixture", Policy: keyPolicy{TierID: "basic"}}
	p, err := g.modelsForKey(k)
	if err != nil {
		t.Fatal(err)
	}
	if len(p.ImageModels) != 1 || p.Tier.Advanced || p.SVG.Enabled || len(p.ImageFeatures[p.DefaultImage].Quality) != 1 || p.Tiers != nil {
		t.Fatal("client profile leaked permissions")
	}
	if _, ok := p.ReasoningModes["max"]; ok {
		t.Fatal("restricted preset was returned")
	}
	for _, tc := range []struct {
		model, effort, mode string
		web                 bool
	}{
		{"gpt-6.1-sol", "max", "max", false}, {"gpt-6-luna", "high", "advanced", false},
		{"gpt-6-luna", "medium", "advanced", false}, {"gpt-6-luna", "medium", "flash", true},
		{"gpt-6-luna", "medium", "polish", false},
	} {
		if g.checkReasoningTier(k, tc.model, tc.effort, tc.mode, tc.web) == nil {
			t.Fatal("restricted reasoning was allowed")
		}
	}
	if err := g.checkReasoningTier(k, "gpt-6-luna", "medium", "flash", false); err != nil {
		t.Fatal(err)
	}
}

func TestKeyVaultDeletionAndTierReferences(t *testing.T) {
	g := testGateway(t)
	installTier(t, g, limitedTier())
	u := addUpstream(t, g, "vault", "", openAIBaseURL)
	k, secret := issue(t, g, accessKey{Name: "secret fixture", Upstream: u.ID, Policy: keyPolicy{TierID: "basic"}})
	value, err := g.store.revealKey(t.Context(), k.ID)
	if err != nil || value != secret {
		t.Fatal("cannot reveal issued key")
	}
	var stored []byte
	if err := g.store.db.QueryRow("SELECT credential FROM key_secrets WHERE key_id=?", k.ID).Scan(&stored); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(stored, []byte(secret)) {
		t.Fatal("secret stored as plaintext")
	}
	profile := *g.appModels()
	profile.Tiers = []featureTier{fullTier()}
	if _, err := g.saveModels(t.Context(), profile); err == nil {
		t.Fatal("deleted an assigned tier")
	}
	id, err := g.store.reserve(t.Context(), route{key: k, upstream: u}, time.Now(), 0)
	if err != nil {
		t.Fatal(err)
	}
	zero := int64(0)
	if err := g.store.finish(t.Context(), id, 200, nil, &zero); err != nil {
		t.Fatal(err)
	}
	if err := g.store.deleteKey(t.Context(), k.ID, nil); err != nil {
		t.Fatal(err)
	}
	if _, _, err := g.store.authenticate(t.Context(), secret); !errors.Is(err, errInvalidKey) {
		t.Fatal("deleted key still authenticates")
	}
	if _, err := g.store.revealKey(t.Context(), k.ID); err == nil {
		t.Fatal("deleted secret still available")
	}
	keys, _ := g.store.listKeys(t.Context())
	if len(keys) != 0 {
		t.Fatal("deleted key still listed")
	}
	var receipts int
	g.store.db.QueryRow("SELECT COUNT(*) FROM requests WHERE key_id=?", k.ID).Scan(&receipts)
	if receipts != 1 {
		t.Fatal("deletion lost accounting receipts")
	}
	if _, err := g.saveModels(context.Background(), profile); err != nil {
		t.Fatal(err)
	}
}

func TestPolicyParserBoundsImagesRejectsDuplicateMetadata(t *testing.T) {
	payload := `{"model":"gpt-image-2.6-flare","images":[{"image_url":"data:image/png;base64,` + strings.Repeat("A", 4<<20) + `"}],"quality":"medium"}`
	data, err := policyJSON(strings.NewReader(payload))
	if err != nil || data["model"] != "gpt-image-2.6-flare" {
		t.Fatal(err)
	}
	if _, err := policyJSON(strings.NewReader(`{"model":"gpt-image-2.6-flare","\u006dodel":"forbidden"}`)); err == nil {
		t.Fatal("ambiguous model keys accepted")
	}
}

func TestPromptTargetUsesNativeControlsAndFormatSpecificBlocks(t *testing.T) {
	svg := imagePromptInstructions(object{"output_format": "svg"})
	gif := imagePromptInstructions(object{"output_format": "gif", "frame_count": float64(12)})
	if !strings.Contains(svg, "路径与分层") || !strings.Contains(svg, "静态 SVG") || !strings.Contains(gif, "运动脚本") || !strings.Contains(gif, "12 帧") || !strings.Contains(gif, "循环衔接") {
		t.Fatal("format-specific prompt instructions missing")
	}
}

func TestTierModelDefaultEffortMatchesBothClientPaths(t *testing.T) {
	g := testGateway(t)
	installTier(t, g, limitedTier())
	settings := *g.appModels()
	settings.ReasoningModes = maps.Clone(settings.ReasoningModes)
	settings.ReasoningModes["flash"] = modelPreset{Model: "gpt-6-luna"}
	if _, err := g.saveModels(t.Context(), settings); err != nil {
		t.Fatal(err)
	}
	k := accessKey{ID: "fixture", Policy: keyPolicy{TierID: "basic"}}
	p, err := g.modelsForKey(k)
	if err != nil {
		t.Fatal(err)
	}
	if p.ReasoningModes["flash"].Effort != "medium" || g.appModels().ReasoningModes["flash"].Effort != "" {
		t.Fatal("client effective effort must not change the administrator's saved default")
	}
	for _, effort := range []string{"", "medium"} {
		if err := g.checkReasoningTier(k, "gpt-6-luna", effort, "flash", false); err != nil {
			t.Fatalf("default effort rejected for client path %q: %v", effort, err)
		}
	}
	if err := g.checkReasoningTier(k, "gpt-6-luna", "high", "flash", false); err == nil {
		t.Fatal("explicit disallowed effort bypassed the tier")
	}

	var forwarded string
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, _ := io.ReadAll(r.Body)
		forwarded = string(data)
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"output_text":"complete"}`)
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "default-effort", "", provider.URL+"/v1")
	_, secret := issue(t, g, accessKey{Name: "basic", Upstream: u.ID, Policy: keyPolicy{TierID: "basic"}})
	mux := http.NewServeMux()
	g.registerProxy(mux)
	for _, payload := range []string{
		`{"model":"gpt-6-luna","input":"untouched"}`,
		`{"model":"gpt-6-luna","input":"untouched","reasoning":{"effort":"medium"}}`,
	} {
		req := httptest.NewRequest("POST", "/v1/responses", strings.NewReader(payload))
		req.Header.Set("Authorization", "Bearer "+secret)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Mirra-Reasoning-Mode", "flash")
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, req)
		if w.Code != http.StatusOK || forwarded != payload {
			t.Fatalf("default-effort POST rejected or changed: %d %s", w.Code, w.Body.String())
		}
	}
}

func TestTierForbiddenConfiguredInstantRemainsDirectGeneration(t *testing.T) {
	for _, tc := range []struct {
		name   string
		preset modelPreset
		modes  []string
	}{
		{"model", modelPreset{Model: "gpt-6.1-sol", Effort: "medium"}, []string{"instant", "flash"}},
		{"effort", modelPreset{Model: "gpt-6-luna", Effort: "high"}, []string{"instant", "flash"}},
		{"mode", modelPreset{Model: "gpt-6-luna", Effort: "medium"}, []string{"flash"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := testGateway(t)
			tier := limitedTier()
			tier.ReasoningModes = tc.modes
			installTier(t, g, tier)
			settings := *g.appModels()
			settings.ReasoningModes = maps.Clone(settings.ReasoningModes)
			settings.ReasoningModes["instant"] = tc.preset
			if _, err := g.saveModels(t.Context(), settings); err != nil {
				t.Fatal(err)
			}
			k := accessKey{ID: "fixture", Policy: keyPolicy{TierID: "basic"}}
			p, err := g.modelsForKey(k)
			if err != nil {
				t.Fatal(err)
			}
			if p.ReasoningModes["instant"] != (modelPreset{Effort: "none"}) {
				t.Fatal("forbidden Instant thinking must fall back to direct image generation")
			}
			if g.appModels().ReasoningModes["instant"] != tc.preset {
				t.Fatal("per-key fallback changed the global Instant setting")
			}
			if err := g.checkReasoningTier(k, tc.preset.Model, tc.preset.Effort, "instant", false); err == nil {
				t.Fatal("forbidden Instant thinking pair still accepted")
			}
		})
	}
}

func TestTierUtilityPairsHonorLowEffortWithoutAdvancedAccess(t *testing.T) {
	g := testGateway(t)
	tier := limitedTier()
	tier.ReasoningEfforts = []string{"low"}
	tier.PromptPolish, tier.ImageEditing, tier.MaxReferences = true, true, 4
	installTier(t, g, tier)
	k := accessKey{ID: "fixture", Policy: keyPolicy{TierID: "basic"}}
	p, err := g.modelsForKey(k)
	if err != nil {
		t.Fatal(err)
	}
	for mode, preset := range map[string]modelPreset{"polish": p.Polish, "continuation": p.Continuation} {
		if preset != (modelPreset{Model: "gpt-6-luna", Effort: "low"}) {
			t.Fatalf("%s did not choose the permitted low-effort pair: %+v", mode, preset)
		}
		if err := g.checkReasoningTier(k, preset.Model, preset.Effort, mode, false); err != nil {
			t.Fatalf("authorized %s pair was rejected: %v", mode, err)
		}
		if err := g.checkReasoningTier(k, preset.Model, "medium", mode, false); err == nil {
			t.Fatalf("%s accepted a disallowed effort", mode)
		}
	}
	if err := g.checkReasoningTier(k, p.Continuation.Model, p.Continuation.Effort, "advanced", false); err == nil {
		t.Fatal("utility access bypassed the advanced-mode restriction")
	}
}
