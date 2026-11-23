package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"image"
	"image/png"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func testGateway(t *testing.T) *gateway {
	t.Helper()
	secret := strings.Repeat("t", 48)
	store, err := openStore(filepath.Join(t.TempDir(), "test.db"), secret)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.db.Close() })
	g := &gateway{store: store, cfg: config{
		token: secret, pin: "362514", imageModel: "gpt-image-2.6-flare", publicBase: "https://mirra.example/openai/v1",
		allowedHosts: map[string]bool{"api.openai.com": true, "127.0.0.1": true},
	}}
	g.egress, err = loadEgress(t.Context(), store, g.cfg)
	if err != nil {
		t.Fatal(err)
	}
	g.client = newUpstreamClient(g.cfg, g.egress)
	return g
}

func addUpstream(t *testing.T, g *gateway, id, pool, url string) upstream {
	t.Helper()
	u := upstream{ID: id, Name: id, URL: url, Pool: pool, Secret: "provider-" + id, Enabled: true}
	if err := g.store.saveUpstream(t.Context(), u); err != nil {
		t.Fatal(err)
	}
	return u
}

func issue(t *testing.T, g *gateway, k accessKey) (accessKey, string) {
	t.Helper()
	k.Enabled = true
	k, secret, _, err := g.store.issue(t.Context(), k, "")
	if err != nil {
		t.Fatal(err)
	}
	return k, secret
}

func TestHourlyAffinity(t *testing.T) {
	g := testGateway(t)
	u := addUpstream(t, g, "a", "openai", openAIBaseURL)
	addUpstream(t, g, "b", "openai", openAIBaseURL)
	addUpstream(t, g, "c", "other-pool", openAIBaseURL)
	k1, _ := issue(t, g, accessKey{Name: "phone+desktop", Upstream: u.ID})
	k2, _ := issue(t, g, accessKey{Name: "different", Upstream: u.ID})
	now := time.Date(2026, 11, 19, 8, 20, 0, 0, time.UTC)
	choose := func(k accessKey, now time.Time) string {
		t.Helper()
		upstream, err := g.store.chooseUpstream(t.Context(), k, u, now)
		if err != nil {
			t.Fatal(err)
		}
		return upstream.ID
	}
	if a, b := choose(k1, now), choose(k2, now); a != "a" || b != "b" {
		t.Fatalf("unexpected assignments: %s %s", a, b)
	}
	if choose(k1, now.Add(39*time.Minute)) != "a" || choose(k2, now.Add(39*time.Minute)) != "b" {
		t.Fatal("assignment changed within the hour")
	}
	if choose(k2, now.Add(time.Hour)) != "b" || choose(k1, now.Add(time.Hour)) != "a" {
		t.Fatal("equal load should keep the previous upstream")
	}
	if _, err := g.store.reserve(t.Context(), route{key: k1, upstream: u}, now.Add(time.Hour), 0); err != nil {
		t.Fatal(err)
	}
	if choose(k1, now.Add(2*time.Hour)) != "b" {
		t.Fatal("the next hour did not account for actual load")
	}
	u.Enabled = false
	if err := g.store.saveUpstream(t.Context(), u); err != nil {
		t.Fatal(err)
	}
	if choose(k1, now.Add(time.Hour)) != "b" {
		t.Fatal("disabled member remained in use")
	}
}

func TestReservationAndBudgets(t *testing.T) {
	g := testGateway(t)
	u := addUpstream(t, g, "quota", "", openAIBaseURL)
	now := time.Date(2026, 11, 23, 8, 0, 0, 0, billingZone)
	k, _ := issue(t, g, accessKey{Name: "bounded", Upstream: u.ID, Policy: keyPolicy{H5: 3000, D1: 4000, D7: 5000}})
	selected := route{key: k, upstream: u}
	var started atomic.Int32
	var group sync.WaitGroup
	for i := 0; i < 12; i++ {
		group.Go(func() {
			_, err := g.store.reserve(t.Context(), selected, now, 1000)
			var quota *quotaError
			if err == nil {
				started.Add(1)
			} else if !errors.As(err, &quota) {
				t.Errorf("reserve: %v", err)
			}
		})
	}
	group.Wait()
	if started.Load() != 3 {
		t.Fatalf("admitted %d requests", started.Load())
	}
	if _, err := g.store.reserve(t.Context(), selected, now.Add(5*time.Hour), 1000); err != nil {
		t.Fatal(err)
	}
	if _, err := g.store.reserve(t.Context(), selected, now.Add(5*time.Hour), 1000); err == nil {
		t.Fatal("24-hour USD cap was not enforced after the 5-hour window advanced")
	}
	if _, err := g.store.reserve(t.Context(), selected, now.Add(24*time.Hour), 1000); err != nil {
		t.Fatal(err)
	}
	if _, err := g.store.reserve(t.Context(), selected, now.Add(24*time.Hour), 1000); err == nil {
		t.Fatal("7-day USD cap was not enforced after the day advanced")
	}
	if _, err := g.store.reserve(t.Context(), selected, now.Add(7*24*time.Hour), 1000); err != nil {
		t.Fatal(err)
	}
	policy := keyPolicy{IPs: []string{"198.51.100.10"}, Total: 1500}
	if err := policy.validate(); err != nil {
		t.Fatal(err)
	}
	budget, _ := issue(t, g, accessKey{Name: "budget", Upstream: u.ID, Policy: policy})
	selected = route{key: budget, upstream: u, clientIP: netip.MustParseAddr("198.51.100.11")}
	if _, err := g.store.reserve(t.Context(), selected, now, 1000); !errors.Is(err, errKeyIP) {
		t.Fatalf("IP allowlist: %v", err)
	}
	selected.clientIP = netip.MustParseAddr("198.51.100.10")
	id, err := g.store.reserve(t.Context(), selected, now, 1000)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := g.store.reserve(t.Context(), selected, now, 1000); err == nil {
		t.Fatal("USD reservation did not count towards the total")
	}
	settled := int64(450)
	if err := g.store.finish(t.Context(), id, http.StatusOK, nil, &settled); err != nil {
		t.Fatal(err)
	}
	if _, err := g.store.reserve(t.Context(), selected, now, 1000); err != nil {
		t.Fatal(err)
	}
}

func TestProxyReportsTruncatedImageStream(t *testing.T) {
	g := testGateway(t)
	const beforeBreak = "data: {\"type\":\"image_generation.completed\",\"b64_json\":\"cut"
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, beforeBreak)
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Error(err)
		}
		panic(http.ErrAbortHandler)
	}))
	defer upstream.Close()
	g.client = upstream.Client()
	account := addUpstream(t, g, "truncated", "", upstream.URL+"/v1")
	key, secret := issue(t, g, accessKey{Name: "truncated", Upstream: account.ID})
	mux := http.NewServeMux()
	g.registerProxy(mux)
	server := httptest.NewServer(mux)
	defer server.Close()
	r, err := http.NewRequestWithContext(t.Context(), "POST", server.URL+"/v1/images/generations", strings.NewReader("{\"stream\":true}"))
	if err != nil {
		t.Fatal(err)
	}
	r.Header.Set("Authorization", "Bearer "+secret)
	reply, err := server.Client().Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer reply.Body.Close()
	body, err := io.ReadAll(reply.Body)
	if !errors.Is(err, io.ErrUnexpectedEOF) || string(body) != beforeBreak {
		t.Fatalf("downstream stream termination: %q (%v)", body, err)
	}
	var status int
	if err := g.store.db.QueryRow("SELECT status FROM requests WHERE key_id = ?", key.ID).Scan(&status); err != nil || status != 502 {
		t.Fatalf("interrupted request recorded status %d: %v", status, err)
	}
}

func TestAdminAndTransparentMetering(t *testing.T) {
	g := testGateway(t)
	g.cfg.imageModel = "test-image"
	const input = `{"model":"test-image","complexity":"high","output_format":"webp","size":"1024x768","mask":"untouched"}`
	const usage = `{"input_tokens":100,"output_tokens":200,"total_tokens":300}`
	const output = "data: {\"type\":\"image_generation.partial_image\",\"b64_json\":\"partial\"}\n\n" +
		"data: {\"type\":\"image_generation.completed\",\"b64_json\":\"final\",\"usage\":" + usage + "}\n\n"
	u := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if string(body) != input || bearer(r) != "provider-test" || r.URL.Path != "/v1/images/generations" {
			t.Errorf("proxy changed the upstream request: %s %s", r.URL.Path, body)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("X-Request-ID", "example-id")
		io.WriteString(w, output)
	}))
	defer u.Close()
	g.client = u.Client()
	g.cfg.reserveMicroUSD = 1000
	g.cfg.pricing = map[string]priceCard{"test-image": {TextInput: 5_000_000, Output: 30_000_000}}
	addUpstream(t, g, "test", "", u.URL+"/v1")
	admin := newAdmin(g).routes(nil)
	request := func(handler http.Handler, method, path, body string, cookie *http.Cookie) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, "http://127.0.0.1"+path, strings.NewReader(body))
		if cookie != nil {
			r.AddCookie(cookie)
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w
	}
	if w := request(admin, "GET", "/admin/state", "", nil); w.Code != 401 {
		t.Fatalf("admin available without PIN: %d", w.Code)
	}
	login := request(admin, "POST", "/admin/login", `{"pin":"362514"}`, nil)
	cookies := login.Result().Cookies()
	if login.Code != 200 || len(cookies) != 1 || !cookies[0].HttpOnly {
		t.Fatalf("PIN login failed: %d %s", login.Code, login.Body.String())
	}
	key := request(admin, "POST", "/admin/keys", `{"name":"test","upstreamId":"test","policy":{"fiveHourMicroUSD":6500},"customKey":"test_custom_secret"}`, cookies[0])
	var issued struct{ Secret string }
	json.Unmarshal(key.Body.Bytes(), &issued)
	if key.Code != 201 || issued.Secret != "sk-mirra-test_custom_secret" {
		t.Fatalf("issue failed: %d", key.Code)
	}
	mux := http.NewServeMux()
	g.registerProxy(mux)
	post := func() *httptest.ResponseRecorder {
		r := httptest.NewRequest("POST", "/v1/images/generations", strings.NewReader(input))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+issued.Secret)
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, r)
		return w
	}
	w := post()
	if w.Code != 200 || w.Body.String() != output || !w.Flushed || w.Header().Get("X-Request-ID") != "example-id" {
		t.Fatalf("bad stream output: %d %s", w.Code, w.Body.String())
	}
	if w := post(); w.Code != 429 || w.Header().Get("Retry-After") == "" {
		t.Fatalf("quota response: %d", w.Code)
	}
	r := httptest.NewRequest("GET", "/v1/usage", nil)
	r.Header.Set("Authorization", "Bearer "+issued.Secret)
	w = httptest.NewRecorder()
	mux.ServeHTTP(w, r)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"total_tokens":300`) || !strings.Contains(w.Body.String(), `"settledMicroUSD":6500`) {
		t.Fatalf("usage missing: %d %s", w.Code, w.Body.String())
	}
}

func TestMobileReferenceEdit(t *testing.T) {
	g := testGateway(t)
	var img bytes.Buffer
	if err := png.Encode(&img, image.NewNRGBA(image.Rect(0, 0, 4, 5))); err != nil {
		t.Fatal(err)
	}
	u := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/images/edits" || bearer(r) != "provider-test" || r.ParseMultipartForm(1<<20) != nil {
			t.Errorf("bad multipart upstream request")
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		for field, want := range map[string]string{"prompt": "保留我的提示词", "complexity": "medium", "size": "1024x768", "quality": "high", "background": "transparent", "output_format": "png"} {
			if got := r.FormValue(field); got != want {
				t.Errorf("%s = %q, want %q", field, got, want)
			}
		}
		ref, _, err := r.FormFile("image[]")
		if err != nil {
			t.Error(err)
		} else {
			data, _ := io.ReadAll(ref)
			ref.Close()
			if !bytes.Equal(data, img.Bytes()) {
				t.Error("reference bytes changed")
			}
		}
		w.Header().Set("Content-Type", "application/json")
		encodeJSON(w, object{"data": []object{{"b64_json": base64.StdEncoding.EncodeToString(img.Bytes())}}})
	}))
	defer u.Close()
	g.client = u.Client()
	addUpstream(t, g, "test", "", u.URL+"/v1")
	_, secret := issue(t, g, accessKey{Name: "photo", Upstream: "test"})
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	mux := http.NewServeMux()
	m.registerRoutes(mux)
	call := func(method, path, token, kind string, body io.Reader) *httptest.ResponseRecorder {
		t.Helper()
		r := httptest.NewRequest(method, path, body)
		r.Header.Set("Authorization", "Bearer "+token)
		r.Header.Set("Content-Type", kind)
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, r)
		return w
	}
	registration := object{"deviceId": "device-owner-key-long", "keyId": "key-a", "name": "photo", "value": secret}
	payload, _ := json.Marshal(registration)
	w := call("POST", "/api/v1/device/register", "", "application/json", bytes.NewReader(payload))
	var token struct{ BearerToken string }
	json.Unmarshal(w.Body.Bytes(), &token)
	if w.Code != 200 || token.BearerToken == "" {
		t.Fatalf("register: %d %s", w.Code, w.Body.String())
	}
	var multipartBody bytes.Buffer
	multipartWriter := multipart.NewWriter(&multipartBody)
	part, _ := multipartWriter.CreateFormFile("file", "ref.png")
	part.Write(img.Bytes())
	multipartWriter.Close()
	w = call("POST", "/api/v1/uploads/references", token.BearerToken, multipartWriter.FormDataContentType(), &multipartBody)
	var uploaded struct{ Resource struct{ ResourceID string } }
	json.Unmarshal(w.Body.Bytes(), &uploaded)
	if w.Code != 200 || uploaded.Resource.ResourceID == "" {
		t.Fatalf("upload: %d %s", w.Code, w.Body.String())
	}
	request := generationInput{KeyID: "key-a", Prompt: "保留我的提示词", ReferenceIDs: []string{uploaded.Resource.ResourceID},
		Options: object{"requestId": "task-a", "aspectRatio": "4:3", "quality": "high", "background": "transparent", "complexity": "medium"}}
	payload, _ = json.Marshal(request)
	w = call("POST", "/api/v1/image-generations", token.BearerToken, "application/json", bytes.NewReader(payload))
	if w.Code != 200 {
		t.Fatalf("submit: %d %s", w.Code, w.Body.String())
	}
	deadline := time.Now().Add(3 * time.Second)
	var item object
	for time.Now().Before(deadline) {
		w = call("GET", "/api/v1/image-generations/task-a", token.BearerToken, "", nil)
		var response struct{ Event object }
		json.Unmarshal(w.Body.Bytes(), &response)
		if response.Event["type"] == "task_result" {
			result := asObject(response.Event["result"])
			if result["ok"] != true {
				t.Fatalf("task failed: %s", w.Body.String())
			}
			item = asObject(result["items"].([]any)[0])
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	assetURL, err := url.Parse(objectString(item, "downloadUrl"))
	if err != nil || assetURL.Path == "" {
		t.Fatalf("missing final asset: %v", item)
	}
	w = call("GET", assetURL.Path, token.BearerToken, "", nil)
	if w.Code != 200 || !bytes.Equal(w.Body.Bytes(), img.Bytes()) {
		t.Fatal("original image bytes missing")
	}
	w = call("GET", assetURL.Path, m.issueDeviceToken("other-device-number", time.Now()), "", nil)
	if w.Code != 404 {
		t.Fatalf("cross-device asset access: %d", w.Code)
	}
}
