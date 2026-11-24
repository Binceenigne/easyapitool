package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type visualFixtureTransport func(*http.Request) (*http.Response, error)

func (f visualFixtureTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func visualFixturePNG() []byte {
	var output bytes.Buffer
	picture := image.NewRGBA(image.Rect(0, 0, 64, 64))
	for y := 0; y < 64; y++ {
		for x := 0; x < 64; x++ {
			picture.Set(x, y, color.RGBA{R: 20, G: 50, B: 180, A: 255})
		}
	}
	png.Encode(&output, picture)
	return output.Bytes()
}
func visualFixtureScope(t *testing.T, g *gateway) *agentVisualScope {
	t.Helper()
	m := &mobile{g: g, instance: t.TempDir(), assets: map[string]asset{}, tasks: map[credentialID]*task{}, changed: make(chan struct{})}
	return newAgentVisualScope(m, credentialID{"visual-device", "visual-task"}, imageOptions{model: "gpt-image-2", size: "1024x1024", quality: "medium", format: "png", background: "opaque", n: 1}, 0)
}
func officialVisualResponse(raw string) object {
	return object{"status": "completed", "output": []object{{"id": "ws-image", "type": "web_search_call", "status": "completed", "action": object{"type": "search", "query": "product image"}, "results": []object{{"type": "image_result", "image_url": raw, "source_website_url": "https://maker.example/product", "caption": "Blue reference product"}}}}}
}

func TestVisualReferenceRegistryAndPublicURLBoundary(t *testing.T) {
	g := testGateway(t)
	scope := visualFixtureScope(t, g)
	scope.lookup = func(context.Context, string) ([]net.IPAddr, error) {
		return []net.IPAddr{{IP: net.ParseIP("8.8.8.8")}}, nil
	}
	for _, raw := range []string{"http://cdn.example/a.png", "https://user:secret@cdn.example/a.png", "https://cdn.example:8443/a.png", "file:///a.png"} {
		if scope.publicURL(t.Context(), raw) == nil {
			t.Fatal("unsafe reference URL accepted", raw)
		}
	}
	if scope.publicURL(t.Context(), "https://cdn.example/a.png") != nil {
		t.Fatal("public HTTPS reference rejected")
	}
	scope.lookup = func(context.Context, string) ([]net.IPAddr, error) {
		return []net.IPAddr{{IP: net.ParseIP("127.0.0.1")}}, nil
	}
	if scope.publicURL(t.Context(), "https://cdn.example/a.png") == nil {
		t.Fatal("private DNS destination accepted")
	}
	raw := "https://cdn.example/a.png"
	observer := &visualResponseObserver{scope: scope, stream: true}
	partial, _ := json.Marshal(object{"type": "response.output_item.done", "item": officialVisualResponse(raw)["output"]})
	observer.Write([]byte("data: " + string(partial) + "\n\n"))
	if len(scope.allowed) != 0 {
		t.Fatal("partial response authorized a reference")
	}
	complete, _ := json.Marshal(object{"type": "response.completed", "response": officialVisualResponse(raw)})
	payload := []byte("data: " + string(complete) + "\n\n")
	for _, part := range [][]byte{payload[:19], payload[19:]} {
		observer.Write(part)
	}
	if len(scope.allowed) != 1 || objectString(scope.allowed[raw], "id") != visualCandidateID(raw) {
		t.Fatal("real image_result not registered")
	}
	if _, err := scope.inspect(t.Context(), accessKey{}, []string{"https://fabricated.example/image.png"}); err == nil {
		t.Fatal("model fabricated reference URL was accepted")
	}
	if _, err := scope.selectReferences(accessKey{}, []string{visualCandidateID(raw)}, "not inspected"); err == nil {
		t.Fatal("uninspected image was adopted")
	}
}

func TestVisualReferenceDownloadsAndSelectionsAreScoped(t *testing.T) {
	g := testGateway(t)
	scope := visualFixtureScope(t, g)
	defer scope.release()
	var requests atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.Header.Get("Authorization") != "" {
			t.Error("credential sent to image host")
		}
		w.Header().Set("Content-Type", "image/png")
		w.Write(visualFixturePNG())
	}))
	defer server.Close()
	address, _ := url.Parse(server.URL)
	transport := server.Client().Transport
	g.client = &http.Client{Transport: visualFixtureTransport(func(r *http.Request) (*http.Response, error) {
		copy := r.Clone(r.Context())
		target := *r.URL
		target.Host = address.Host
		copy.URL = &target
		return transport.RoundTrip(copy)
	})}
	scope.lookup = func(context.Context, string) ([]net.IPAddr, error) {
		return []net.IPAddr{{IP: net.ParseIP("8.8.8.8")}}, nil
	}
	raw := "https://cdn.example/real.png"
	scope.registerResponse(officialVisualResponse(raw))
	result, err := scope.inspect(t.Context(), accessKey{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	candidates := arrayObjects(result["candidates"])
	if len(candidates) != 1 || !strings.HasPrefix(objectString(candidates[0], "previewDataUrl"), "data:image/jpeg;base64,") {
		t.Fatal("verified thumbnail missing", result)
	}
	again, err := scope.inspect(t.Context(), accessKey{}, []string{raw})
	if err != nil || requests.Load() != 1 || arrayObjects(again["candidates"])[0]["previewDataUrl"] != nil {
		t.Fatal("inspected preview was downloaded/re-attached twice")
	}
	id := visualCandidateID(raw)
	selected, err := scope.selectReferences(accessKey{}, []string{id}, "Preserve the product shape")
	if err != nil || len(arrayObjects(selected["webReferences"])) != 1 || len(scope.adopted()) != 1 {
		t.Fatal("valid adoption failed", err)
	}
	scope.options.format = "svg"
	if _, err := scope.selectReferences(accessKey{}, []string{id}, "vector"); err == nil {
		t.Fatal("SVG adopted raster references")
	}
	if _, err := scope.selectReferences(accessKey{}, nil, "Use only visual research for SVG"); err != nil {
		t.Fatal(err)
	}
	if len(scope.adopted()) != 0 {
		t.Fatal("empty selection kept stale references")
	}
}

func TestVisualReferenceCanonical403UsesOnlyItsOfficialThumbnail(t *testing.T) {
	g := testGateway(t)
	scope := visualFixtureScope(t, g)
	defer scope.release()
	var requests atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.URL.Path == "/canonical.png" {
			w.WriteHeader(403)
			return
		}
		if r.URL.Path != "/official-thumb.png" {
			t.Error("unregistered image request", r.URL.Path)
			w.WriteHeader(404)
			return
		}
		w.Header().Set("Content-Type", "image/png")
		w.Write(visualFixturePNG())
	}))
	defer server.Close()
	address, _ := url.Parse(server.URL)
	transport := server.Client().Transport
	g.client = &http.Client{Transport: visualFixtureTransport(func(r *http.Request) (*http.Response, error) {
		copy := r.Clone(r.Context())
		target := *r.URL
		target.Host = address.Host
		copy.URL = &target
		return transport.RoundTrip(copy)
	})}
	scope.lookup = func(context.Context, string) ([]net.IPAddr, error) {
		return []net.IPAddr{{IP: net.ParseIP("8.8.8.8")}}, nil
	}
	canonical, thumbnail := "https://cdn.example/canonical.png", "https://cdn.example/official-thumb.png"
	response := officialVisualResponse(canonical)
	arrayObjects(arrayObjects(response["output"])[0]["results"])[0]["thumbnail_url"] = thumbnail
	scope.registerResponse(response)
	result, err := scope.inspect(t.Context(), accessKey{}, []string{canonical})
	if err != nil {
		t.Fatal(err)
	}
	items := arrayObjects(result["candidates"])
	if requests.Load() != 2 || len(items) != 1 || objectString(items[0], "id") != visualCandidateID(canonical) || items[0]["imageUrl"] != canonical || items[0]["actualDownloadedUrl"] != thumbnail || items[0]["sourceUrl"] != "https://maker.example/product" {
		t.Fatal("canonical/thumbnail provenance lost", result)
	}
	if !strings.HasPrefix(objectString(items[0], "previewDataUrl"), "data:image/jpeg;base64,") {
		t.Fatal("fallback was not decoded as an actual image")
	}
	if _, err = scope.selectReferences(accessKey{}, []string{visualCandidateID(canonical)}, "Use the verified same-result thumbnail"); err != nil || len(scope.adopted()) != 1 {
		t.Fatal("verified thumbnail could not be adopted", err)
	}
}

func TestRealSDKVisualToolLoopAdoptsVerifiedImageThroughGo(t *testing.T) {
	python := os.Getenv("MIRRA_TEST_SDK_PYTHON")
	if python == "" {
		t.Skip("set MIRRA_TEST_SDK_PYTHON for real SDK visual tool loop")
	}
	g := testGateway(t)
	scope := visualFixtureScope(t, g)
	defer scope.release()
	raw := "https://cdn.example/product.png"
	candidateID := visualCandidateID(raw)
	imageBytes := visualFixturePNG()
	var modelCalls, imageCalls atomic.Int32
	var sawVision atomic.Bool
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/product.png" {
			if r.Header.Get("Authorization") != "" {
				t.Error("upstream API key leaked to image host")
			}
			w.Header().Set("Content-Type", "image/png")
			w.Write(imageBytes)
			return
		}
		if r.URL.Path == "/v1/images/edits" {
			imageCalls.Add(1)
			parts, err := r.MultipartReader()
			if err != nil {
				t.Error(err)
				return
			}
			found := false
			for {
				part, err := parts.NextPart()
				if err == io.EOF {
					break
				}
				if err != nil {
					t.Error(err)
					break
				}
				if part.FileName() != "" {
					decoded, _, err := image.Decode(part)
					if err != nil {
						t.Error(err)
					} else {
						rr, gg, bb, _ := decoded.At(3, 3).RGBA()
						found = rr == 20*257 && gg == 50*257 && bb == 180*257
					}
				}
			}
			if !found {
				t.Error("adopted reference did not reach image multipart")
			}
			writeJSON(w, 200, object{"data": []object{{"b64_json": base64.StdEncoding.EncodeToString(imageBytes)}}, "usage": object{"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
			return
		}
		if r.URL.Path != "/v1/responses" {
			t.Error("unexpected path", r.URL.Path)
			w.WriteHeader(404)
			return
		}
		var input object
		if json.NewDecoder(r.Body).Decode(&input) != nil {
			t.Error("invalid model request")
		}
		call := modelCalls.Add(1)
		var output []object
		switch call {
		case 1:
			output = append(arrayObjects(officialVisualResponse(raw)["output"]), object{"id": "fc-inspect", "type": "function_call", "status": "completed", "call_id": "inspect-one", "name": "inspect_visual_references", "arguments": "{\"image_urls\":[]}"})
		case 2:
			if inputImageCount(input["input"]) != 1 {
				t.Error("SDK did not see inspected ToolOutputImage")
			}
			sawVision.Store(true)
			arguments, _ := json.Marshal(object{"candidate_ids": []string{candidateID}, "rationale": "Preserve the real product silhouette"})
			output = []object{{"id": "fc-select", "type": "function_call", "status": "completed", "call_id": "select-one", "name": "select_visual_references", "arguments": string(arguments)}}
		default:
			output = []object{{"id": "msg-final", "type": "message", "role": "assistant", "status": "completed", "content": []object{{"type": "output_text", "text": "Faithful blue product on a studio background", "annotations": []object{}}}}}
		}
		response := object{"id": fmt.Sprintf("resp-visual-%d", call), "created_at": 1, "object": "response", "model": "gpt-6.1-terra", "status": "completed", "error": nil, "incomplete_details": nil, "instructions": nil, "metadata": object{}, "parallel_tool_calls": true, "temperature": 1, "tool_choice": "auto", "tools": []object{}, "top_p": 1, "output": output, "usage": object{"input_tokens": 10, "output_tokens": 3, "total_tokens": 13, "input_tokens_details": object{"cached_tokens": 2}, "output_tokens_details": object{"reasoning_tokens": 0}}}
		w.Header().Set("Content-Type", "text/event-stream")
		encoded, _ := json.Marshal(object{"type": "response.completed", "response": response, "sequence_number": 0})
		fmt.Fprintf(w, "data: %s\n\n", encoded)
	}))
	defer provider.Close()
	address, _ := url.Parse(provider.URL)
	transport := provider.Client().Transport
	g.client = &http.Client{Transport: visualFixtureTransport(func(r *http.Request) (*http.Response, error) {
		if r.URL.Hostname() == "cdn.example" {
			copy := r.Clone(r.Context())
			target := *r.URL
			target.Host = address.Host
			copy.URL = &target
			return transport.RoundTrip(copy)
		}
		return transport.RoundTrip(r)
	})}
	scope.lookup = func(context.Context, string) ([]net.IPAddr, error) {
		return []net.IPAddr{{IP: net.ParseIP("8.8.8.8")}}, nil
	}
	upstream := addUpstream(t, g, "visual", "", provider.URL+"/v1")
	key, secret := issue(t, g, accessKey{Name: "visual SDK", Upstream: upstream.ID})
	selected, err := g.resolveRoute(t.Context(), secret, netip.MustParseAddr("192.0.2.29"), true)
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
	g.cfg.agentWorkerToken = strings.Repeat("visual-integration", 3)
	g.cfg.upstreamTimeout = 40 * time.Second
	folder, _ := os.Getwd()
	var stderr bytes.Buffer
	cmd := exec.Command(python, filepath.Join(folder, "agent_runtime", "worker.py"))
	cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Dir(folder), "MIRRA_AGENT_WORKER_HOST=127.0.0.1", fmt.Sprintf("MIRRA_AGENT_WORKER_PORT=%d", port), "MIRRA_AGENT_WORKER_TOKEN="+g.cfg.agentWorkerToken, "MIRRA_AGENT_PROXY_BASE_URL="+g.cfg.agentProxyBase)
	cmd.Stdout = io.Discard
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { cmd.Process.Kill(); cmd.Wait() }()
	ready := false
	for start := time.Now(); time.Since(start) < 10*time.Second; time.Sleep(30 * time.Millisecond) {
		request, _ := http.NewRequest("GET", g.cfg.agentWorkerURL+"/healthz", nil)
		request.Header.Set("Authorization", "Bearer "+g.cfg.agentWorkerToken)
		response, err := http.DefaultClient.Do(request)
		if err == nil {
			response.Body.Close()
			if response.StatusCode == 200 {
				ready = true
				break
			}
		}
	}
	if !ready {
		t.Fatal("worker not ready", stderr.String())
	}
	ctx := context.WithValue(t.Context(), agentVisualScopeKey{}, scope)
	var mapped []string
	ctx = context.WithValue(ctx, agentEventSinkKey{}, func(e object) { mapped = append(mapped, objectString(e, "type")) })
	prompt, clientUsage, _, err := g.rewrite(ctx, selected, "gpt-6.1-terra", "high", "Render the product", true, object{"reasoningMode": "advanced"})
	if err != nil {
		t.Fatalf("visual SDK loop failed: %v; worker: %s", err, stderr.String())
	}
	if prompt != "Faithful blue product on a studio background" || modelCalls.Load() != 3 || !sawVision.Load() || len(scope.adopted()) != 1 {
		t.Fatalf("loop/adoption mismatch: %s calls=%d refs=%d", prompt, modelCalls.Load(), len(scope.adopted()))
	}
	if _, err := scope.mobile.renderImage(t.Context(), scope.id, keyInput{KeyID: key.ID, Value: secret, BaseURL: g.cfg.publicBase}, selected.clientIP, prompt, scope.options, scope.adopted(), 0); err != nil {
		t.Fatal("reference generation", err)
	}
	if imageCalls.Load() != 1 {
		t.Fatal("adopted image was not rendered")
	}
	var records int
	g.store.db.QueryRow("SELECT COUNT(*) FROM requests WHERE key_id=?", key.ID).Scan(&records)
	if records != 4 {
		t.Fatal("SDK/tool loop bypassed metering", records)
	}
	if scope.stats["requestCount"] != float64(3) || scope.stats["toolCallCount"] != float64(3) {
		t.Fatal("request/tool accounting missing", scope.stats)
	}
	if len(scope.turns) != 3 {
		t.Fatal("reasoning turns were not persisted")
	}
	if clientUsage["inputTokens"] != int64(30) || clientUsage["outputTokens"] != int64(9) || clientUsage["totalTokens"] != int64(39) || clientUsage["callCount"] != int64(3) || clientUsage["hasTokenUsage"] != true || clientUsage["input_tokens"] != float64(30) {
		t.Fatal("multi-turn usage aliases did not reach the client", clientUsage)
	}
}

func TestSDKClientUsageKeepsRawAndAddsClientAliases(t *testing.T) {
	raw := object{"input_tokens": float64(30), "output_tokens": float64(9), "total_tokens": float64(39), "requests": float64(3), "input_tokens_details": object{"cached_tokens": float64(6)}}
	client := sdkClientUsage(raw)
	if client["inputTokens"] != int64(30) || client["outputTokens"] != int64(9) || client["totalTokens"] != int64(39) || client["cachedTokens"] != int64(6) || client["callCount"] != int64(3) || client["hasTokenUsage"] != true {
		t.Fatal("client token/call usage missing", client)
	}
	if client["input_tokens"] != raw["input_tokens"] || raw["inputTokens"] != nil {
		t.Fatal("provider ledger fields were changed")
	}
	if sdkClientUsage(object{"requests": float64(3)})["hasTokenUsage"] != false {
		t.Fatal("missing token usage was reported as measured")
	}
}
