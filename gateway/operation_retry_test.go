package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/png"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func retryPNG(t *testing.T) string {
	t.Helper()
	var picture bytes.Buffer
	if err := png.Encode(&picture, image.NewRGBA(image.Rect(0, 0, 2, 2))); err != nil {
		t.Fatal(err)
	}
	return base64.StdEncoding.EncodeToString(picture.Bytes())
}

func TestImageRetriesOnlyFailedItemAndPersistsTerminalResult(t *testing.T) {
	g := testGateway(t)
	encoded := retryPNG(t)
	var calls atomic.Int32
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempt := calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if attempt == 2 {
			w.Header().Set("Retry-After", "0.01")
			writeJSON(w, 503, object{"error": object{"code": "server_error", "message": "temporary renderer unavailable"}})
			return
		}
		writeJSON(w, 200, object{"data": []object{{"b64_json": encoded}}, "usage": object{"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}})
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "image-retry", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "image retry", Upstream: u.ID})
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	key := keyInput{KeyID: k.ID, Value: secret, BaseURL: g.cfg.publicBase}
	id := credentialID{"owner", "one-set"}
	jobCtx, jobCancel := context.WithCancel(ctx)
	m.tasks[id] = &task{cancel: jobCancel}
	options, err := g.imageOptions(object{"imageCount": float64(2), "stream": false}, false, g.appModels())
	if err != nil {
		t.Fatal(err)
	}
	m.workers.Add(1)
	m.runGeneration(jobCtx, jobCancel, id, key, netip.MustParseAddr("192.0.2.3"), generationInput{Prompt: "two images"}, options, nil, g.appModels())
	terminal := m.tasks[id].lastEvent
	result := asObject(terminal["result"])
	items := result["items"].([]object)
	if calls.Load() != 3 || result["ok"] != true || items[0]["attempts"] != 1 || items[1]["attempts"] != 2 {
		t.Fatalf("completed image was regenerated or failed image not retried: calls=%d result=%v", calls.Load(), result)
	}
	var retry object
	for _, event := range m.events {
		if event.data["type"] == "request_retry" {
			retry = event.data
		}
	}
	if retry["itemIndex"] != 1 || retry["attempt"] != 2 || retry["resetPreview"] != true || retry["delayMs"] != int64(10) {
		t.Fatal("retry progress metadata missing", retry)
	}
	var admissions int
	if err := g.store.db.QueryRow("SELECT COUNT(*) FROM requests WHERE key_id=?", k.ID).Scan(&admissions); err != nil || admissions != 3 {
		t.Fatal("each image attempt must be metered separately", admissions, err)
	}
	stored, err := g.store.terminalEvent(t.Context(), id)
	if err != nil || asObject(stored["result"])["ok"] != true {
		t.Fatal("task terminal state not persisted", err)
	}
	var ciphertext []byte
	if err := g.store.db.QueryRow("SELECT value FROM task_terminal_events WHERE owner=? AND task_id=?", id.owner, id.id).Scan(&ciphertext); err != nil || bytes.Contains(ciphertext, []byte("two images")) {
		t.Fatal("terminal state was not encrypted", err)
	}
	if _, err := g.store.terminalEvent(t.Context(), credentialID{"another-owner", id.id}); err == nil {
		t.Fatal("another owner read a persisted task")
	}
	delete(m.tasks, id)
	w := httptest.NewRecorder()
	r := httptest.NewRequest("GET", "/api/v1/image-generations/one-set", nil)
	r.SetPathValue("id", id.id)
	m.taskState(w, r, id.owner)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"type":"task_result"`) {
		t.Fatal("persisted terminal event not returned after in-memory task eviction")
	}
}

func TestImageRetryExhaustionAndPermanentFailuresHavePreciseCauses(t *testing.T) {
	for _, test := range []struct {
		status   int
		code     string
		expected int
	}{{503, "renderer_unavailable", 3}, {400, "invalid_size", 1}, {429, "insufficient_quota", 1}, {403, "permission_denied", 1}} {
		t.Run(test.code, func(t *testing.T) {
			g := testGateway(t)
			var calls atomic.Int32
			provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				w.Header().Set("Retry-After", "0.001")
				w.Header().Set("X-Request-Id", "image-cause-42")
				writeJSON(w, test.status, object{"error": object{"code": test.code, "message": "specific upstream rejection"}})
			}))
			defer provider.Close()
			g.client = provider.Client()
			u := addUpstream(t, g, "failure", "", provider.URL+"/v1")
			k, secret := issue(t, g, accessKey{Name: "failure", Upstream: u.ID})
			ctx, cancel := context.WithCancel(t.Context())
			m := newMobile(ctx, g, t.TempDir())
			defer func() { cancel(); m.workers.Wait() }()
			options, _ := g.imageOptions(object{"stream": false}, false, g.appModels())
			_, err := m.renderImageWithRetry(ctx, credentialID{"owner", "failure"}, keyInput{KeyID: k.ID, Value: secret, BaseURL: g.cfg.publicBase}, netip.MustParseAddr("192.0.2.3"), "test", options, nil, 0)
			failure, ok := err.(*operationFailure)
			if !ok || failure.Attempts != test.expected || failure.MaxAttempts != 3 || failure.Status != test.status || failure.Code != test.code || failure.DiagnosticID != "image-cause-42" || !strings.Contains(failure.LastCause, "specific upstream rejection") || int(calls.Load()) != test.expected {
				t.Fatalf("unexpected terminal cause/call count: %+v calls=%d", failure, calls.Load())
			}
		})
	}
}

func TestPartialPreviewDoesNotPreventRetryOrCountAsFinal(t *testing.T) {
	g := testGateway(t)
	encoded := retryPNG(t)
	var calls atomic.Int32
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.Header().Set("Content-Type", "text/event-stream")
			jsonData, _ := json.Marshal(object{"type": "image_generation.partial_image", "b64_json": encoded})
			w.Write([]byte("data: " + string(jsonData) + "\n\n"))
			return
		}
		writeJSON(w, 200, object{"data": []object{{"b64_json": encoded}}})
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "partial", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "partial", Upstream: u.ID})
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	id := credentialID{"owner", "partial"}
	m.tasks[id] = &task{cancel: cancel}
	options, _ := g.imageOptions(nil, false, g.appModels())
	item, err := m.renderImageWithRetry(ctx, id, keyInput{KeyID: k.ID, Value: secret, BaseURL: g.cfg.publicBase}, netip.MustParseAddr("192.0.2.3"), "test", options, nil, 0)
	if err != nil || calls.Load() != 2 || item["attempts"] != 2 {
		t.Fatal("partial preview was mistaken for final output", err, item)
	}
	partial, retried := false, false
	for _, event := range m.events {
		partial = partial || event.data["type"] == "item_partial"
		retried = retried || event.data["type"] == "request_retry" && event.data["resetPreview"] == true
	}
	if !partial || !retried {
		t.Fatal("preview or preview-reset retry event missing")
	}
}

func TestRetryWaitHonorsCancellationAndRedactsSecrets(t *testing.T) {
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	start := time.Now()
	if err := waitForRetry(ctx, time.Minute); err != context.Canceled || time.Since(start) > time.Second {
		t.Fatal("cancel did not interrupt retry delay")
	}
	message := safeFailureCause("sk-project_abcdefghijk Bearer secret-token lease-abcdefghijklmnop")
	if strings.Contains(message, "abcdefghijk") || strings.Contains(message, "secret-token") || strings.Contains(message, "abcdefghijklmnop") {
		t.Fatal("failure metadata exposed a credential", message)
	}
	if parseRetryAfter("0.25", time.Now()) != 250*time.Millisecond {
		t.Fatal("fractional Retry-After lost")
	}
}
