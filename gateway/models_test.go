package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/netip"
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
			if body["model"] != "text-fixture" || asObject(body["reasoning"])["effort"] != "high" {
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
	settings.Reasoning["luna"] = modelPreset{Model: "text-fixture", Effort: "high"}
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
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"defaultImage":"image-fixture"`) {
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
}
