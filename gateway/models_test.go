package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/color"
	"image/gif"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"strings"
	"sync/atomic"
	"testing"
)

func TestProxyProviderAddress(t *testing.T) {
	g := testGateway(t)
	input := proxySettings{Mode: "custom", Scope: "openai", URL: "socks5://proxy.example:8001:fixture:12345"}
	view, err := g.egress.save(t.Context(), input)
	if err != nil {
		t.Fatal(err)
	}
	if g.egress.settings.URL != "socks5://fixture:12345@proxy.example:8001" ||
		view["address"] != "socks5://proxy.example:8001" || view["authenticated"] != true {
		t.Fatal("SOCKS export address was not normalized or masked")
	}
	config, err := loadEgress(t.Context(), g.store, g.cfg)
	if err != nil || config.view()["address"] != view["address"] {
		t.Fatal("normalized proxy did not survive reload")
	}
	u, err := parseProxyURL("socks5h://[2001:db8::1]:1080:user:p:/%ass")
	if err != nil {
		t.Fatal(err)
	}
	password, _ := u.User.Password()
	if u.Host != "[2001:db8::1]:1080" || password != "p:/%ass" {
		t.Fatal("SOCKS export credentials or IPv6 host were changed")
	}
	for _, bad := range []string{
		"socks5://proxy.example:65536:user:password",
		"socks5://proxy.example:1080/:user:password",
		"socks5://proxy.example:1080::1234",
		"http://proxy.example:1080:user:1234",
	} {
		if _, err := parseProxyURL(bad); err == nil {
			t.Fatalf("accepted malformed address %q", bad)
		}
	}
}

func TestManagedModelConfiguration(t *testing.T) {
	g := testGateway(t)
	vector := `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M0 0H32V32Z"/></svg>`
	var seenImage, seenReasoning atomic.Bool
	up := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Header.Get("Authorization") != "Bearer provider-mock" {
			t.Error("proxy did not use the selected upstream account")
		}
		switch r.URL.Path {
		case "/v1/models":
			w.Write([]byte(`{"data":[{"id":"image-fixture"},{"id":"text-fixture"},{"id":"svg-fixture"},{"id":"image-fixture"}]}`))
		case "/v1/responses":
			var body object
			json.NewDecoder(r.Body).Decode(&body)
			want := map[string]string{"text-fixture": "high", "advanced-fixture": "max", "quick-fixture": "minimal"}
			model := objectString(body, "model")
			if want[model] == "" || asObject(body["reasoning"])["effort"] != want[model] {
				t.Error("configured reasoning preset was not sent upstream")
			}
			seenReasoning.Store(true)
			w.Write([]byte(`{"output_text":"配置后的提示词","status":"completed"}`))
		case "/v1/images/generations":
			var body object
			json.NewDecoder(r.Body).Decode(&body)
			svg := asObject(body["svg"])
			if body["model"] != "image-fixture" || body["output_format"] != "svg" || svg["model"] != "svg-fixture" ||
				asObject(svg["reasoning"])["effort"] != "low" || body["stream"] != nil {
				t.Error("configured image and SVG author parameters were not sent upstream")
			}
			seenImage.Store(true)
			json.NewEncoder(w).Encode(object{"data": []object{{"b64_json": base64.StdEncoding.EncodeToString([]byte(vector))}}})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer up.Close()
	g.client = up.Client()
	account := addUpstream(t, g, "mock", "", up.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "model-test", Upstream: account.ID})

	admin := newAdmin(g).routes(nil)
	request := func(method, path string, body []byte, cookie *http.Cookie) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, "http://localhost"+path, bytes.NewReader(body))
		if cookie != nil {
			r.AddCookie(cookie)
		}
		w := httptest.NewRecorder()
		admin.ServeHTTP(w, r)
		return w
	}
	login := request("POST", "/admin/login", []byte(`{"pin":"362514"}`), nil)
	cookie := login.Result().Cookies()[0]
	path := "/admin/upstreams/mock/models"
	if request("GET", path, nil, nil).Code != http.StatusUnauthorized {
		t.Fatal("model discovery is available without a PIN session")
	}
	if w := request("GET", path, nil, cookie); w.Code != 200 || !strings.Contains(w.Body.String(), `["image-fixture","svg-fixture","text-fixture"]`) {
		t.Fatalf("model discovery failed: %d", w.Code)
	}

	settings := modelDefaults(g.cfg)
	settings.DefaultImage, settings.ImageModels = "image-fixture", []string{"image-fixture"}
	settings.SVG = svgSettings{Enabled: true, modelPreset: modelPreset{Model: "svg-fixture", Effort: "low"}}
	settings.Reasoning["luna"] = modelPreset{Model: "advanced-fixture", Effort: "medium"}
	settings.ReasoningModes["flash"] = modelPreset{Model: "text-fixture", Effort: "high"}
	settings.ReasoningModes["instant"] = modelPreset{Model: "quick-fixture", Effort: "minimal"}
	settings.ImageFeatures = map[string]imageFeatures{"image-fixture": {Complexity: true, Animation: true}}
	payload, _ := json.Marshal(settings)
	if w := request("POST", "/admin/models", payload, cookie); w.Code != 200 || !strings.Contains(w.Body.String(), `"svg":{"enabled":true,"model":"svg-fixture","effort":"low"}`) {
		t.Fatalf("saving models failed: %d", w.Code)
	}
	if w := request("POST", "/admin/models", payload, cookie); w.Code != http.StatusConflict {
		t.Fatal("a stale model configuration revision was accepted")
	}
	if err := g.loadModels(t.Context()); err != nil || g.appModels().DefaultImage != "image-fixture" {
		t.Fatal("saved model configuration did not survive reload")
	}
	r := httptest.NewRequest("GET", "/v1/usage", nil)
	r.Header.Set("Authorization", "Bearer "+secret)
	w := httptest.NewRecorder()
	g.proxyUsage(w, r)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"defaultImage":"image-fixture"`) ||
		!strings.Contains(w.Body.String(), `"flash":{"model":"text-fixture","effort":"high"}`) ||
		!strings.Contains(w.Body.String(), `"image-fixture":{"complexity":false,"animation":false,"svg":false,"quality":[],"sizeMode":"auto"}`) {
		t.Fatal("client usage lacks managed model settings")
	}
	if _, err := g.imageOptions(object{"imageModel": "misconfigured"}, false, g.appModels()); err == nil {
		t.Fatal("unpublished image model was accepted by the Android gateway")
	}
	if o, err := g.imageOptions(object{}, false, nil); err != nil || o.model != g.cfg.imageModel {
		t.Fatal("provider key lost its default")
	}
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	input := generationInput{KeyID: k.ID, Prompt: "试图", Options: object{"reasoningMode": "flash", "output_format": "svg"}}
	job := credentialID{"device", "job"}
	key := keyInput{KeyID: k.ID, Value: secret, BaseURL: g.cfg.publicBase}
	ip := netip.MustParseAddr("127.0.0.1")
	prompt, _, err := m.preparePrompt(ctx, job, key, ip, input, g.appModels())
	if err != nil || prompt != "配置后的提示词" || !seenReasoning.Load() {
		t.Fatalf("managed reasoning failed: %v", err)
	}
	options, err := g.imageOptions(input.Options, false, g.appModels())
	if err != nil {
		t.Fatal(err)
	}
	result, err := m.renderImage(ctx, job, key, ip, prompt, options, nil, 0)
	if err != nil || !seenImage.Load() || result["mimeType"] != "image/svg+xml" {
		t.Fatalf("managed SVG generation failed: %v", err)
	}
	for _, mode := range []object{
		{"reasoningMode": "advanced", "reasoningModel": "luna", "reasoningEffort": "max"},
		{"reasoningMode": "instant"},
	} {
		seenReasoning.Store(false)
		input.Options = mode
		if _, _, err := m.preparePrompt(ctx, job, key, ip, input, g.appModels()); err != nil || !seenReasoning.Load() {
			t.Fatalf("managed reasoning %v failed: %v", mode, err)
		}
	}
}

func TestOpenAIModelCapabilities(t *testing.T) {
	g := testGateway(t)
	updated := modelDefaults(g.cfg)
	updated.ImageFeatures["gpt-image-2"] = imageFeatures{Complexity: true, Animation: true, Quality: []string{"max"}}
	updated.ReasoningModes["flash"] = modelPreset{Model: "gpt-6-luna", Effort: "minimal"}
	saved, err := g.saveModels(t.Context(), updated)
	if err != nil {
		t.Fatal(err)
	}
	image2 := saved.ImageFeatures["gpt-image-2"]
	if image2.Complexity || image2.Animation || image2.SVG || len(image2.Quality) != 3 {
		t.Fatalf("the basic Image 2 settings were overridden: %+v", image2)
	}
	flare := saved.ImageFeatures["gpt-image-2.6-flare"]
	if !flare.Complexity || !flare.Animation || !flare.SVG || len(flare.Quality) != 5 {
		t.Fatalf("the documented Flare controls are missing: %+v", flare)
	}
	if saved.ReasoningModes["flash"].Effort != "medium" || supportedReasoningEffort("gpt-6-luna", "max") != "max" ||
		supportedReasoningEffort("gpt-5.4", "max") != "none" {
		t.Fatalf("the default reasoning efforts were not applied: %+v", saved.ReasoningModes["flash"])
	}
	for _, option := range []object{
		{"imageModel": "gpt-image-2", "quality": "xhigh"},
		{"imageModel": "gpt-image-2", "complexity": "medium"},
		{"imageModel": "gpt-image-2", "output_format": "gif"},
	} {
		if _, err := g.imageOptions(option, false, saved); err == nil {
			t.Fatalf("an unsupported image setting was accepted: %+v", option)
		}
	}
}

func TestLegacyModelSettingsSeedNormalModes(t *testing.T) {
	g := testGateway(t)
	legacy := modelDefaults(g.cfg)
	legacy.Reasoning["terra"] = modelPreset{Model: "existing-custom-terra", Effort: "high"}
	legacy.ReasoningModes, legacy.ImageFeatures = nil, nil
	payload, _ := json.Marshal(legacy)
	if _, err := g.store.db.ExecContext(t.Context(), "INSERT INTO gateway_settings(name,value) VALUES ('models',?)", payload); err != nil {
		t.Fatal(err)
	}
	if err := g.loadModels(t.Context()); err != nil {
		t.Fatal(err)
	}
	modes := g.appModels().ReasoningModes
	if len(modes) != 6 || modes["instant"].Model != "" || modes["extra"] != legacy.Reasoning["terra"] {
		t.Fatalf("legacy presets were not preserved: %+v", modes)
	}
}

func TestMobileImageStreamPublishesNamedPreview(t *testing.T) {
	g := testGateway(t)
	m := newMobile(t.Context(), g, t.TempDir())
	var picture bytes.Buffer
	if err := png.Encode(&picture, image.NewRGBA(image.Rect(0, 0, 2, 2))); err != nil {
		t.Fatal(err)
	}
	encoded := base64.StdEncoding.EncodeToString(picture.Bytes())
	job := credentialID{"device", "stream"}
	_, cancel := context.WithCancel(t.Context())
	defer cancel()
	m.tasks[job] = &task{cancel: cancel}
	stream := "event: image_generation.partial_image\r\ndata: {\"b64_json\":\r\ndata: \"" + encoded + "\"}\r\n\r\n" +
		"event: image_generation.completed\ndata: {\"b64_json\":\"" + encoded + "\",\"usage\":{\"total_tokens\":42}}\n\n"
	response := &http.Response{Header: http.Header{"Content-Type": {"text/event-stream"}}, Body: io.NopCloser(strings.NewReader(stream))}
	defer response.Body.Close()
	final, count, err := m.readImageResponse(response, job, 0, 3, "png")
	if err != nil || final.format != "png" || count == nil || count.Total != 42 {
		t.Fatalf("named completion was missed: %v", err)
	}
	if event := m.tasks[job].lastEvent; event["type"] != "item_partial" || event["partialIndex"] != 1 {
		t.Fatalf("named preview was missed: %+v", event)
	}
}

func TestMobileImageCompressionUsesFixedQuality(t *testing.T) {
	g := testGateway(t)
	var picture bytes.Buffer
	if err := png.Encode(&picture, image.NewRGBA(image.Rect(0, 0, 2, 2))); err != nil {
		t.Fatal(err)
	}
	up := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body object
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		format := objectString(body, "output_format")
		var expected any
		if oneOf(format, "webp", "jpeg") {
			expected = float64(75)
		}
		if body["output_compression"] != expected {
			t.Errorf("%s used compression %v, want %v", format, body["output_compression"], expected)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(object{"data": []object{{"b64_json": base64.StdEncoding.EncodeToString(picture.Bytes())}}})
	}))
	defer up.Close()
	g.client = up.Client()
	account := addUpstream(t, g, "compression", "", up.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "compression", Upstream: account.ID})
	m := newMobile(t.Context(), g, t.TempDir())
	key := keyInput{KeyID: k.ID, Value: secret, BaseURL: g.cfg.publicBase}
	for _, input := range []object{
		{"outputPreset": "small"}, {"output_format": "jpeg"}, {"output_format": "webp", "output_compression": float64(42)}, {"output_format": "png"},
	} {
		options, err := g.imageOptions(input, false, g.appModels())
		if err != nil {
			t.Fatal(err)
		}
		if _, err := m.renderImage(t.Context(), credentialID{"device", options.format}, key, netip.MustParseAddr("127.0.0.1"), "test", options, nil, 0); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAnimatedImageGenerateAndEdit(t *testing.T) {
	g := testGateway(t)
	palette := color.Palette{color.Black, color.White}
	first := image.NewPaletted(image.Rect(0, 0, 2, 2), palette)
	second := image.NewPaletted(first.Bounds(), palette)
	second.SetColorIndex(0, 0, 1)
	var animation bytes.Buffer
	if err := gif.EncodeAll(&animation, &gif.GIF{Image: []*image.Paletted{first, second}, Delay: []int{35, 35}}); err != nil {
		t.Fatal(err)
	}
	up := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		want := map[string]string{"output_format": "gif", "frame_count": "2", "frame_duration_ms": "350", "complexity": "medium"}
		switch r.URL.Path {
		case "/v1/images/edits":
			for field, value := range want {
				if r.FormValue(field) != value {
					t.Errorf("wrong edit field %s = %q", field, r.FormValue(field))
				}
			}
			if r.FormValue("stream") != "" || r.FormValue("partial_images") != "" {
				t.Error("GIF edits cannot be streamed")
			}
		case "/v1/images/generations":
			var body object
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if body["output_format"] != "gif" || body["frame_count"] != float64(2) || body["frame_duration_ms"] != float64(350) ||
				body["complexity"] != "medium" || body["stream"] != nil || body["partial_images"] != nil {
				t.Errorf("wrong GIF generation fields: %v", body)
			}
		default:
			t.Errorf("unexpected animation route: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(object{"data": []object{{"b64_json": base64.StdEncoding.EncodeToString(animation.Bytes())}}})
	}))
	defer up.Close()
	g.client = up.Client()
	account := addUpstream(t, g, "animation", "", up.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "animate", Upstream: account.ID})
	m := newMobile(t.Context(), g, t.TempDir())
	key := keyInput{KeyID: k.ID, Value: secret, BaseURL: g.cfg.publicBase}
	ip := netip.MustParseAddr("127.0.0.1")
	var pngBytes bytes.Buffer
	if err := png.Encode(&pngBytes, first); err != nil {
		t.Fatal(err)
	}
	ref, err := m.saveAsset("device", &pngBytes, false, false)
	if err != nil {
		t.Fatal(err)
	}
	for _, refs := range [][]asset{nil, {ref}} {
		options, err := g.imageOptions(object{"output_format": "gif", "complexity": "medium", "frame_count": float64(2), "frame_duration_ms": float64(350)}, len(refs) != 0, g.appModels())
		if err != nil {
			t.Fatal(err)
		}
		item, err := m.renderImage(t.Context(), credentialID{"device", "gif"}, key, ip, "test", options, refs, 0)
		if err != nil || item["mimeType"] != "image/gif" || item["resourceId"] != item["previewResourceId"] {
			t.Fatalf("GIF original/preview failed: %v", err)
		}
		m.mu.Lock()
		original := m.assets[objectString(item, "resourceId")]
		m.mu.Unlock()
		saved, err := os.ReadFile(original.path)
		if err != nil || !bytes.Equal(saved, animation.Bytes()) {
			t.Fatal("the generated animation did not survive asset storage")
		}
	}
	if _, err := g.imageOptions(object{"output_format": "gif", "frame_count": float64(17)}, false, nil); err == nil {
		t.Fatal("accepted an invalid frame count")
	}
	if _, err := g.imageOptions(object{"output_format": "png", "frame_duration_ms": float64(200)}, false, nil); err == nil {
		t.Fatal("allowed an animation parameter for a still image")
	}
}
