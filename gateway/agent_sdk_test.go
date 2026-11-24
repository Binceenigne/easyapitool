package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func agentTestBody(model, effort string) string {
	encoded, _ := json.Marshal(object{"model": model, "input": "private prompt", "reasoning": object{"effort": effort}, "store": false})
	return string(encoded)
}

func TestAgentLeasePreservesClientIPAndBillsEverySDKCall(t *testing.T) {
	g := testGateway(t)
	var calls atomic.Int32
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != "/v1/responses" || r.Header.Get("Authorization") != "Bearer provider-sdk" {
			t.Error("lease did not use its captured Go upstream route")
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"model":"gpt-6.1-terra","usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15},"output":[]}`)
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "sdk", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "original IP", Upstream: u.ID, Policy: keyPolicy{IPs: []string{"198.51.100.7/32"}}})
	selected, err := g.resolveRoute(t.Context(), secret, netip.MustParseAddr("198.51.100.7"), true)
	if err != nil {
		t.Fatal(err)
	}
	lease, revoke := g.newAgentLease(t.Context(), selected, "gpt-6.1-terra", "high", "advanced", false)
	mux := http.NewServeMux()
	g.registerProxy(mux)
	server := httptest.NewServer(mux)
	defer server.Close()
	call := func(model, effort string) *http.Response {
		req, _ := http.NewRequest("POST", server.URL+"/internal/agent/v1/responses", strings.NewReader(agentTestBody(model, effort)))
		req.Header.Set("Authorization", "Bearer "+lease)
		req.Header.Set("Content-Type", "application/json")
		resp, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		return resp
	}
	for range 2 {
		if resp := call("gpt-6.1-terra", "high"); resp.StatusCode != 200 {
			t.Fatal("SDK proxy", resp.StatusCode)
		}
	}
	var total, input int
	if err := g.store.db.QueryRow("SELECT COUNT(*),COALESCE(SUM(input_tokens),0) FROM requests WHERE key_id=?", k.ID).Scan(&total, &input); err != nil || total != 2 || input != 20 {
		t.Fatalf("SDK model calls were not separately metered: %d %d %v", total, input, err)
	}
	if resp := call("gpt-6.1-sol", "high"); resp.StatusCode != 403 {
		t.Fatal("lease permitted another model")
	}
	if resp := call("gpt-6.1-terra", "max"); resp.StatusCode != 403 {
		t.Fatal("lease permitted another effort")
	}
	policy := k.Policy
	policy.IPs = []string{"127.0.0.1/32"}
	if _, err := g.store.db.Exec("UPDATE access_keys SET policy=? WHERE id=?", policy.encode(), k.ID); err != nil {
		t.Fatal(err)
	}
	if resp := call("gpt-6.1-terra", "high"); resp.StatusCode != 403 {
		t.Fatal("worker loopback bypassed the original client IP restriction")
	}
	if calls.Load() != 2 {
		t.Fatal("out-of-scope or IP-denied SDK request reached provider")
	}
	revoke()
	if resp := call("gpt-6.1-terra", "high"); resp.StatusCode != 401 {
		t.Fatal("revoked lease remained usable")
	}
}

func TestAgentLeaseRejectsExternalPeersToolsAndCancellation(t *testing.T) {
	g := testGateway(t)
	ctx, cancel := context.WithCancel(t.Context())
	lease, revoke := g.newAgentLease(ctx, route{upstream: upstream{URL: openAIBaseURL, Secret: "external-user-key"}}, "gpt-6.1-terra", "high", "advanced", false)
	defer revoke()
	for _, test := range []struct {
		remote, body string
		status       int
	}{
		{"203.0.113.9:1234", agentTestBody("gpt-6.1-terra", "high"), 403},
		{"127.0.0.1:1234", `{"model":"gpt-6.1-terra","reasoning":{"effort":"high"},"tools":[{"type":"image_generation"}]}`, 403},
		{"127.0.0.1:1234", `{"model":"gpt-6.1-terra","reasoning":{"effort":"high"},"tools":[{"type":"web_search"}]}`, 403},
	} {
		r := httptest.NewRequest("POST", "http://localhost/internal/agent/v1/responses", strings.NewReader(test.body))
		r.RemoteAddr = test.remote
		r.Header.Set("Authorization", "Bearer "+lease)
		w := httptest.NewRecorder()
		g.agentProxy(w, r)
		if w.Code != test.status {
			t.Fatalf("private scope: %d %s", w.Code, w.Body.String())
		}
	}
	cancel()
	r := httptest.NewRequest("POST", "http://localhost/internal/agent/v1/responses", strings.NewReader(agentTestBody("gpt-6.1-terra", "high")))
	r.RemoteAddr = "127.0.0.1:1234"
	r.Header.Set("Authorization", "Bearer "+lease)
	w := httptest.NewRecorder()
	g.agentProxy(w, r)
	if w.Code != 401 {
		t.Fatal("cancelled run retained a usable lease")
	}
}

func TestSDKBridgeStreamsEventsUsesLeaseAndNeverRetriesWholeAgent(t *testing.T) {
	g := testGateway(t)
	var modelCalls, workerCalls atomic.Int32
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		modelCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"model":"gpt-6.1-terra","usage":{"input_tokens":4,"output_tokens":2,"total_tokens":6},"output":[]}`)
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "sdk-bridge", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "SDK bridge", Upstream: u.ID})
	selected, err := g.resolveRoute(t.Context(), secret, netip.MustParseAddr("192.0.2.9"), true)
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	g.registerProxy(mux)
	proxy := httptest.NewServer(mux)
	defer proxy.Close()
	g.cfg.agentProxyBase = proxy.URL + "/internal/agent/v1"
	g.cfg.agentWorkerToken = strings.Repeat("w", 48)
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		workerCalls.Add(1)
		if r.Header.Get("Authorization") != "Bearer "+g.cfg.agentWorkerToken {
			t.Error("worker authentication missing")
		}
		var data struct {
			Request object `json:"request"`
		}
		_ = json.NewDecoder(r.Body).Decode(&data)
		if _, exists := data.Request["max_output_tokens"]; exists {
			t.Error("Go overrode the shared SDK model output budget")
		}
		key := objectString(data.Request, "api_key")
		if !strings.HasPrefix(key, "lease-") || key == secret || key == u.Secret {
			t.Error("worker received a persistent credential")
		}
		for range 2 {
			req, _ := http.NewRequest("POST", objectString(data.Request, "base_url")+"/responses", strings.NewReader(agentTestBody("gpt-6.1-terra", "high")))
			req.Header.Set("Authorization", "Bearer "+key)
			req.Header.Set("Content-Type", "application/json")
			response, err := proxy.Client().Do(req)
			if err != nil {
				t.Error(err)
				return
			}
			io.Copy(io.Discard, response.Body)
			response.Body.Close()
			if response.StatusCode != 200 {
				t.Error("SDK admission", response.StatusCode)
			}
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		io.WriteString(w, "{\"type\":\"text_delta\",\"delta\":\"draft\"}\n{\"type\":\"retry\",\"attempt\":2,\"maxAttempts\":3,\"cause\":\"upstream timeout\"}\n{\"type\":\"result\",\"text\":\"finished prompt\",\"usage\":{\"input_tokens\":8},\"webSearchUsed\":false}\n")
	}))
	defer worker.Close()
	g.cfg.agentWorkerURL = worker.URL
	var events []object
	ctx := context.WithValue(t.Context(), agentEventSinkKey{}, func(event object) { events = append(events, event) })
	text, usage, web, err := g.rewrite(ctx, selected, "gpt-6.1-terra", "high", "original", false, object{"reasoningMode": "advanced"})
	if err != nil || text != "finished prompt" || usage["input_tokens"] != float64(8) || web || workerCalls.Load() != 1 || modelCalls.Load() != 2 {
		t.Fatalf("SDK bridge result/accounting mismatch: %s %v %v", text, usage, err)
	}
	if len(events) != 3 || events[0]["type"] != "react_turn_delta" || events[1]["type"] != "react_summary_delta" || events[2]["type"] != "request_retry" {
		t.Fatal("SDK events were not mapped for the clients")
	}
	var admitted int
	if err := g.store.db.QueryRow("SELECT COUNT(*) FROM requests WHERE key_id=?", k.ID).Scan(&admitted); err != nil || admitted != 2 {
		t.Fatal("per-call SDK admission missing", admitted, err)
	}
	if len(g.agentLeases) != 0 {
		t.Fatal("SDK run leaked its short-lived lease")
	}
}

func TestSDKBridgeCancellationPostsWorkerCancel(t *testing.T) {
	g := testGateway(t)
	g.cfg.agentProxyBase = "http://127.0.0.1:12345/internal/agent/v1"
	g.cfg.agentWorkerToken = strings.Repeat("x", 48)
	cancelled := make(chan struct{}, 1)
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		if r.URL.Path == "/cancel" {
			cancelled <- struct{}{}
			writeJSON(w, 200, object{"ok": true})
			return
		}
		<-r.Context().Done()
	}))
	defer worker.Close()
	g.cfg.agentWorkerURL = worker.URL
	ctx, cancel := context.WithTimeout(t.Context(), 80*time.Millisecond)
	defer cancel()
	_, _, _, err := g.rewrite(ctx, route{upstream: upstream{URL: openAIBaseURL}}, "gpt-6.1-terra", "high", "cancel", false)
	if err == nil {
		t.Fatal("cancelled SDK run succeeded")
	}
	select {
	case <-cancelled:
	case <-time.After(3 * time.Second):
		t.Fatal("SDK worker did not receive cancellation")
	}
}

func TestSDKWorkerAdmissionRetriesBusyButNeverReplaysAcceptedRun(t *testing.T) {
	for _, accepted := range []bool{true, false} {
		t.Run(map[bool]string{true: "busy_then_accepted", false: "accepted_then_interrupted"}[accepted], func(t *testing.T) {
			g := testGateway(t)
			g.cfg.agentProxyBase = "http://127.0.0.1:12345/internal/agent/v1"
			g.cfg.agentWorkerToken = strings.Repeat("x", 48)
			var calls atomic.Int32
			worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/run" {
					writeJSON(w, 200, object{"ok": true})
					return
				}
				call := calls.Add(1)
				io.Copy(io.Discard, r.Body)
				if accepted && call < 3 {
					w.Header().Set("Retry-After", "0.001")
					writeJSON(w, 503, object{"error": "agent_worker_busy"})
					return
				}
				w.Header().Set("Content-Type", "application/x-ndjson")
				if accepted {
					io.WriteString(w, "{\"type\":\"result\",\"text\":\"ready\"}\n")
				} else {
					io.WriteString(w, "{\"type\":\"text_delta\",\"delta\":\"partial\"}\n")
				}
			}))
			defer worker.Close()
			g.cfg.agentWorkerURL = worker.URL
			text, _, _, err := g.rewrite(t.Context(), route{upstream: upstream{URL: openAIBaseURL}}, "gpt-6.1-terra", "high", "test", false)
			if accepted && (err != nil || text != "ready" || calls.Load() != 3) {
				t.Fatal("busy pre-run admissions not retried", err, calls.Load())
			}
			if !accepted && (err == nil || calls.Load() != 1) {
				t.Fatal("Go replayed a run after SDK/model work may have started", err, calls.Load())
			}
		})
	}
}

func TestDirectCallerCredentialStaysInsideGoAgentLease(t *testing.T) {
	g := testGateway(t)
	var calls atomic.Int32
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if bearer(r) != "direct-caller-key" {
			t.Error("direct key route changed")
		}
		writeJSON(w, 200, object{"output": []object{}})
	}))
	defer provider.Close()
	g.client = provider.Client()
	selected := route{upstream: upstream{ID: "legacy", URL: provider.URL + "/v1", Secret: "direct-caller-key"}, clientIP: netip.MustParseAddr("192.0.2.1")}
	lease, revoke := g.newAgentLease(t.Context(), selected, "gpt-6.1-terra", "high", "polish", false)
	defer revoke()
	mux := http.NewServeMux()
	g.registerProxy(mux)
	server := httptest.NewServer(mux)
	defer server.Close()
	req, _ := http.NewRequest("POST", server.URL+"/internal/agent/v1/responses", strings.NewReader(agentTestBody("gpt-6.1-terra", "high")))
	req.Header.Set("Authorization", "Bearer "+lease)
	req.Header.Set("Content-Type", "application/json")
	resp, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 || calls.Load() != 1 {
		t.Fatal("direct/legacy caller did not use existing Go upstream/egress route")
	}
	var records int
	if err := g.store.db.QueryRow("SELECT COUNT(*) FROM requests").Scan(&records); err != nil || records != 0 {
		t.Fatal("a direct caller was charged against an unrelated distributed key")
	}
}
