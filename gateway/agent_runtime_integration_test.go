package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// Optional dependency test: the provider is an isolated TLS fixture, never a
// paid upstream. Set MIRRA_TEST_SDK_PYTHON to the dedicated SDK virtualenv.
func TestRealSDKWorkerThroughMeteredGoLease(t *testing.T) {
	python := os.Getenv("MIRRA_TEST_SDK_PYTHON")
	if python == "" {
		t.Skip("set MIRRA_TEST_SDK_PYTHON for the real Agents SDK integration")
	}
	g := testGateway(t)
	var calls atomic.Int32
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := calls.Add(1)
		var input object
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Error(err)
		}
		if input["store"] != false {
			t.Error("SDK enabled upstream response storage")
		}
		tools, _ := input["tools"].([]any)
		found := false
		for _, raw := range tools {
			found = found || objectString(asObject(raw), "type") == "web_search"
		}
		if !found {
			t.Error("official Agents SDK WebSearchTool was not sent")
		}
		if call == 1 {
			w.Header().Set("Retry-After", "0.001")
			writeJSON(w, 503, object{"error": object{"code": "server_error", "message": "fixture temporary failure"}})
			return
		}
		message := object{"id": "msg-1", "type": "message", "role": "assistant", "status": "completed", "content": []object{{"type": "output_text", "text": "SDK completed image prompt", "annotations": []object{}}}}
		response := object{"id": "resp-runtime", "created_at": 1, "object": "response", "model": "gpt-6.1-terra", "status": "completed", "error": nil, "incomplete_details": nil, "instructions": nil, "metadata": object{}, "parallel_tool_calls": true, "temperature": 1, "tool_choice": "auto", "tools": []object{}, "top_p": 1, "output": []object{message}, "usage": object{"input_tokens": 10, "output_tokens": 3, "total_tokens": 13, "input_tokens_details": object{"cached_tokens": 2}, "output_tokens_details": object{"reasoning_tokens": 0}}}
		created := object{}
		for key, value := range response {
			created[key] = value
		}
		created["status"], created["output"] = "in_progress", []object{}
		w.Header().Set("Content-Type", "text/event-stream")
		for _, event := range []object{{"type": "response.created", "response": created, "sequence_number": 0}, {"type": "response.output_text.delta", "delta": "SDK completed image prompt", "content_index": 0, "output_index": 0, "item_id": "msg-1", "sequence_number": 1}, {"type": "response.completed", "response": response, "sequence_number": 2}} {
			encoded, _ := json.Marshal(event)
			fmt.Fprintf(w, "data: %s\n\n", encoded)
		}
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "runtime", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "runtime", Upstream: u.ID})
	selected, err := g.resolveRoute(t.Context(), secret, netip.MustParseAddr("192.0.2.19"), true)
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	g.registerProxy(mux)
	proxy := httptest.NewServer(mux)
	defer proxy.Close()
	g.cfg.agentProxyBase = proxy.URL + "/internal/agent/v1"
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	g.cfg.agentWorkerURL = fmt.Sprintf("http://127.0.0.1:%d", port)
	g.cfg.agentWorkerToken = strings.Repeat("integration", 5)
	g.cfg.upstreamTimeout = 30 * time.Second
	workDir, _ := os.Getwd()
	var stderr bytes.Buffer
	cmd := exec.Command(python, filepath.Join(workDir, "agent_runtime", "worker.py"))
	cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Dir(workDir), "MIRRA_AGENT_WORKER_HOST=127.0.0.1", fmt.Sprintf("MIRRA_AGENT_WORKER_PORT=%d", port), "MIRRA_AGENT_WORKER_TOKEN="+g.cfg.agentWorkerToken, "MIRRA_AGENT_PROXY_BASE_URL="+g.cfg.agentProxyBase)
	cmd.Stdout = io.Discard
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { cmd.Process.Kill(); cmd.Wait() }()
	ready := false
	for start := time.Now(); time.Since(start) < 10*time.Second; time.Sleep(30 * time.Millisecond) {
		req, _ := http.NewRequest("GET", g.cfg.agentWorkerURL+"/healthz", nil)
		req.Header.Set("Authorization", "Bearer "+g.cfg.agentWorkerToken)
		resp, err := http.DefaultClient.Do(req)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == 200 {
				ready = true
				break
			}
		}
	}
	if !ready {
		t.Fatal("SDK worker did not become ready")
	}
	var retryEvents int
	ctx := context.WithValue(t.Context(), agentEventSinkKey{}, func(event object) {
		if event["type"] == "request_retry" {
			retryEvents++
		}
	})
	prompt, _, _, err := g.rewrite(ctx, selected, "gpt-6.1-terra", "high", "prepare a landscape prompt", true, object{"reasoningMode": "advanced"})
	if err != nil || prompt != "SDK completed image prompt" || calls.Load() != 2 || retryEvents != 1 {
		t.Fatalf("real SDK bridge failed: text=%q calls=%d retryEvents=%d err=%v", prompt, calls.Load(), retryEvents, err)
	}
	var records int
	if err := g.store.db.QueryRow("SELECT COUNT(*) FROM requests WHERE key_id=?", k.ID).Scan(&records); err != nil || records != 2 {
		t.Fatal("real SDK retries bypassed per-call admission", records, err)
	}
}
