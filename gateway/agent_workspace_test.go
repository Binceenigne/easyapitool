package main

import (
	"bytes"
	"context"
	"fmt"
	"image"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"testing"
)

func TestWorkspaceHistoryAndAssetsStayInsideOwnerAndParent(t *testing.T) {
	g := testGateway(t)
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	first, _ := m.saveAsset("owner", bytes.NewReader(visualFixturePNG()), false, false)
	second, _ := m.saveAsset("owner", bytes.NewReader(visualFixturePNG()), false, false)
	outside, _ := m.saveAsset("owner", bytes.NewReader(visualFixturePNG()), false, false)
	foreign, _ := m.saveAsset("another-owner", bytes.NewReader(visualFixturePNG()), false, false)
	parent := object{"setId": "parent", "sessionId": "workspace-one", "parentSetId": "", "originalPrompt": "original prompt", "items": []object{assetInfo(first, first), assetInfo(second, second), assetInfo(foreign, foreign)}}
	if err := g.store.saveTerminalEvent(t.Context(), credentialID{"owner", "parent"}, object{"result": parent}); err != nil {
		t.Fatal(err)
	}
	scope := newAgentVisualScope(m, credentialID{"owner", "new-round"}, imageOptions{format: "png"}, 1)
	scope.initial = []asset{first}
	scope.workspace = scope.newWorkspace(t.Context(), object{"parentSetId": "parent", "sessionId": "untrusted-other-workspace"}, 16)
	defer scope.release()
	w := scope.workspace
	listed, err := w.list(accessKey{}, "assets", 0, 50)
	if err != nil || listed["workspaceId"] != "workspace-one" || listed["total"] != 2 {
		t.Fatal("workspace history mismatch", listed, err)
	}
	encoded := strings.TrimSpace(objectString(w.metadata[first.id], "path"))
	if !strings.HasPrefix(encoded, "workspace://") {
		t.Fatal("actual filesystem path leaked into model metadata", encoded)
	}
	for _, id := range []string{outside.id, foreign.id, "../../etc/passwd"} {
		if _, err := w.view(accessKey{}, []string{id}); err == nil {
			t.Fatal("out of workspace view allowed", id)
		}
		if _, err := w.selectInputs(accessKey{}, "edit", []string{id}, "choose"); err == nil {
			t.Fatal("out of workspace selection allowed", id)
		}
	}
	if w.viewed[second.id] {
		t.Fatal("listing pretended the model had viewed an image")
	}
	if _, err := w.selectInputs(accessKey{}, "edit", []string{second.id}, "Use the other registered parent output"); err != nil {
		t.Fatal("agent cannot choose its own registered reference", err)
	}
	if w.viewed[second.id] {
		t.Fatal("selecting a file pretended it had been viewed")
	}
	viewed, err := w.view(accessKey{}, []string{second.id})
	if err != nil || !strings.HasPrefix(objectString(viewed["assets"].([]object)[0], "previewDataUrl"), "data:image/jpeg;base64,") {
		t.Fatal("workspace did not return actual pixels", viewed, err)
	}
	viewed, err = w.view(accessKey{}, []string{second.id})
	if err != nil || viewed["assets"].([]object)[0]["previewDataUrl"] != nil {
		t.Fatal("workspace repeated already-viewed pixels", viewed, err)
	}
	if _, err := w.selectInputs(accessKey{}, "generate", nil, "New composition without references"); err != nil || len(w.selectedAssets()) != 0 {
		t.Fatal("agent could not choose unconditioned generation", err)
	}
}

func TestVisualEmptySelectionRemainsAnAgentDecision(t *testing.T) {
	g := testGateway(t)
	scope := visualFixtureScope(t, g)
	scope.registerResponse(officialVisualResponse("https://cdn.example/product.png"))
	result, err := scope.selectReferences(accessKey{}, nil, "Only text research is useful for this task")
	if err != nil || len(result["webReferences"].([]object)) != 0 {
		t.Fatal("empty selection was forced into inspection", result, err)
	}
	meta := scope.referenceMetadata()
	if meta["webSelectionRationale"] != "Only text research is useful for this task" || len(meta["webInspectedReferences"].([]object)) != 0 {
		t.Fatal("empty selection metadata was fabricated", meta)
	}
}

func TestWorkspaceIncludesAllSameSessionBranchesBeyondOneStoragePage(t *testing.T) {
	g := testGateway(t)
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	for index := 0; index < 105; index++ {
		id := fmt.Sprintf("round-%03d", index)
		result := object{"setId": id, "sessionId": "full-workspace", "roundNumber": index + 1, "originalPrompt": id, "items": []object{}}
		if err := g.store.saveTerminalEvent(t.Context(), credentialID{"owner", id}, object{"result": result}); err != nil {
			t.Fatal(err)
		}
	}
	if err := g.store.saveTerminalEvent(t.Context(), credentialID{"owner", "not-this-session"}, object{"result": object{"sessionId": "other-workspace", "items": []object{}}}); err != nil {
		t.Fatal(err)
	}
	scope := newAgentVisualScope(m, credentialID{"owner", "current"}, imageOptions{format: "png"}, 0)
	scope.workspace = scope.newWorkspace(t.Context(), object{"parentSetId": "round-001"}, 16)
	defer scope.release()
	page, err := scope.workspace.list(accessKey{}, "history", 100, 50)
	if err != nil || page["total"] != 105 || page["resultCount"] != 5 || page["hasMore"] != false {
		t.Fatal("workspace siblings were cut off or mixed with another session", page, err)
	}
	if len(scope.workspace.selectedAssets()) != 0 {
		t.Fatal("listing siblings silently selected their outputs")
	}
}

func TestInstantOnlyTierConfiguredUtilityIsMeteredAndCannotUpgradeModel(t *testing.T) {
	g := testGateway(t)
	tier := limitedTier()
	tier.ImageEditing = true
	tier.MaxReferences = 2
	tier.ReasoningModels = []string{}
	tier.ReasoningEfforts = []string{}
	tier.ReasoningModes = []string{"instant"}
	tier.RPMLimit = 2
	installTier(t, g, tier)
	preset := g.minimumContinuationPreset()
	g.cfg.reserveMicroUSD = 5000
	g.cfg.pricing = map[string]priceCard{preset.Model: {TextInput: 1000000, TextCached: 100000, Output: 5000000}}
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, object{"model": preset.Model, "output": []object{}, "usage": object{"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}})
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "instant-only", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "instant-only", Upstream: u.ID, Policy: keyPolicy{TierID: "basic", H5: 1000000}})
	profile, err := g.modelsForKey(k)
	if err != nil {
		t.Fatal(err)
	}
	if profile.Continuation != preset {
		t.Fatal("image editing lost its exact server utility pair", profile.Continuation)
	}
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	a, _ := m.saveAsset("owner", bytes.NewReader(visualFixturePNG()), false, false)
	scope := newAgentVisualScope(m, credentialID{"owner", "new"}, imageOptions{format: "png"}, 1)
	scope.initial = []asset{a}
	scope.workspace = scope.newWorkspace(t.Context(), object{"parentSetId": "uploaded-parent"}, 2)
	defer scope.release()
	internal := context.WithValue(ctx, agentVisualScopeKey{}, scope)
	if err := g.checkReasoningTier(k, preset.Model, preset.Effort, "continuation", false); err != nil {
		t.Fatal("internal Instant continuation was denied", err)
	}
	for _, test := range []struct {
		ctx                 context.Context
		model, effort, mode string
	}{{ctx, preset.Model, preset.Effort, "instant"}, {internal, "gpt-6.1-sol", "high", "continuation"}, {internal, preset.Model, "max", "continuation"}, {internal, preset.Model, preset.Effort, "advanced"}, {ctx, preset.Model, preset.Effort, ""}} {
		if g.checkReasoningTier(k, test.model, test.effort, test.mode, false) == nil {
			t.Fatal("hidden utility scope was widened", test.model, test.effort, test.mode)
		}
	}
	selected, err := g.resolveRoute(ctx, secret, netip.MustParseAddr("192.0.2.8"), true)
	if err != nil {
		t.Fatal(err)
	}
	lease, revoke := g.newAgentLease(internal, selected, preset.Model, preset.Effort, "continuation", false)
	defer revoke()
	mux := http.NewServeMux()
	g.registerProxy(mux)
	server := httptest.NewServer(mux)
	defer server.Close()
	for _, denied := range []struct{ model, effort, mode string }{{"gpt-6.1-sol", "high", "continuation"}, {preset.Model, "max", "continuation"}, {preset.Model, preset.Effort, "instant"}, {preset.Model, preset.Effort, "advanced"}, {preset.Model, preset.Effort, ""}} {
		request, _ := http.NewRequest("POST", server.URL+"/v1/responses", strings.NewReader(agentTestBody(denied.model, denied.effort)))
		request.Header.Set("Authorization", "Bearer "+secret)
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("X-Mirra-Reasoning-Mode", denied.mode)
		response, err := server.Client().Do(request)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if response.StatusCode != 403 {
			t.Fatal("public utility header widened language permissions", denied, response.StatusCode)
		}
	}
	desktop, _ := http.NewRequest("POST", server.URL+"/v1/responses", strings.NewReader(agentTestBody(preset.Model, preset.Effort)))
	desktop.Header.Set("Authorization", "Bearer "+secret)
	desktop.Header.Set("Content-Type", "application/json")
	desktop.Header.Set("X-Mirra-Reasoning-Mode", "continuation")
	desktopResponse, err := server.Client().Do(desktop)
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, desktopResponse.Body)
	desktopResponse.Body.Close()
	if desktopResponse.StatusCode != 200 {
		t.Fatal("desktop public utility path was rejected", desktopResponse.StatusCode)
	}
	call := func() int {
		req, _ := http.NewRequest("POST", server.URL+"/internal/agent/v1/responses", strings.NewReader(agentTestBody(preset.Model, preset.Effort)))
		req.Header.Set("Authorization", "Bearer "+lease)
		req.Header.Set("Content-Type", "application/json")
		response, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, response.Body)
		response.Body.Close()
		return response.StatusCode
	}
	if status := call(); status != 200 {
		t.Fatal("private SDK admission rejected the minimal internal model", status)
	}
	if status := call(); status != 429 {
		t.Fatal("internal utility bypassed all-time RPM", status)
	}
	var count, tokens, cost int
	if err := g.store.db.QueryRow("SELECT COUNT(*),SUM(total_tokens),SUM(cost_micro) FROM requests WHERE key_id=?", k.ID).Scan(&count, &tokens, &cost); err != nil || count != 2 || tokens != 30 || cost != 70 {
		t.Fatal("internal utility usage was not precisely charged", count, tokens, cost, err)
	}
}

func TestWorkspaceViewUsesOwnedImageBudgetForThreeByTwo4K(t *testing.T) {
	g := testGateway(t)
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	picture := image.NewRGBA(image.Rect(0, 0, 3840, 2560))
	var encoded bytes.Buffer
	if png.Encode(&encoded, picture) != nil {
		t.Fatal("4K PNG fixture failed")
	}
	asset, err := m.saveAsset("owner", &encoded, true, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := g.store.saveTerminalEvent(ctx, credentialID{"owner", "parent"}, object{"result": object{"sessionId": "4k-workspace", "items": []object{assetInfo(asset, asset)}}}); err != nil {
		t.Fatal(err)
	}
	scope := newAgentVisualScope(m, credentialID{"owner", "next"}, imageOptions{format: "png"}, 0)
	scope.workspace = scope.newWorkspace(ctx, object{"parentSetId": "parent"}, 16)
	defer scope.release()
	if _, err := scope.initialInput("Only change the jacket"); err != nil {
		t.Fatal("owned 3:2 4K parent incorrectly used external 9MP cap", err)
	}
	scope.workspace.viewed = map[string]bool{}
	result, err := scope.workspace.view(accessKey{}, []string{asset.id})
	if err != nil || len(result["assets"].([]object)) != 1 || !strings.HasPrefix(objectString(result["assets"].([]object)[0], "previewDataUrl"), "data:image/jpeg;base64,") {
		t.Fatal("owned 3:2 4K workspace view failed", result, err)
	}
}
