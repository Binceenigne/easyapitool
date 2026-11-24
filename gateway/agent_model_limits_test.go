package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"strings"
	"testing"
)

func TestAgentModelRegistryMatchesSharedSDKSource(t *testing.T) {
	source, err := os.ReadFile("../mirra_agents/model_limits.json")
	if err != nil || !bytes.Equal(source, agentModelLimitsJSON) {
		t.Fatal("run go generate to synchronize the shared Agent model registry", err)
	}
}

func TestAgentModelPolicyUsesDocumentedOutputAndUsableInput(t *testing.T) {
	fields := object{}
	applyAgentModelPolicy(fields, "gpt-6.1-terra")
	if fields["max_output_tokens"] != int64(128000) {
		t.Fatal("official maximum output budget was not applied", fields)
	}
	policy := fields["context_management"].([]object)[0]
	if policy["compact_threshold"] != int64(829800) {
		t.Fatal("compaction must use 90% of usable input, with output space reserved", fields)
	}
	for _, model := range []string{"custom-model", "gpt-6.1-terra-private", "gpt-6.1-terra-2099-01-01"} {
		fields := object{}
		applyAgentModelPolicy(fields, model)
		if len(fields) != 0 {
			t.Fatal("an undocumented model inherited fabricated limits", model, fields)
		}
	}
}

func TestAgentModelPolicyParityForEveryCanonicalAndExactAlias(t *testing.T) {
	registry := officialAgentModelRegistry
	all := map[string]agentModelLimit{}
	for model, limits := range registry.Models {
		all[model] = limits
	}
	for alias, canonical := range registry.Aliases {
		all[alias] = registry.Models[canonical]
	}
	for model, expected := range all {
		fields := object{}
		applyAgentModelPolicy(fields, model)
		if fields["max_output_tokens"] != expected.MaxOutputTokens {
			t.Fatal("model output budget differs from shared registry", model, fields)
		}
		if !expected.ServerCompaction {
			if _, exists := fields["context_management"]; exists {
				t.Fatal("undocumented model compaction was enabled", model)
			}
			continue
		}
		input := expected.MaxInputTokens
		if input == 0 {
			input = expected.ContextWindow
		}
		threshold := min(input, expected.ContextWindow-expected.MaxOutputTokens) * 9 / 10
		if fields["context_management"].([]object)[0]["compact_threshold"] != threshold {
			t.Fatal("compaction input budget differs from shared registry", model, fields)
		}
	}
}

func TestVisualObserverRetainsReferencesInLargeCompactedResponses(t *testing.T) {
	g := testGateway(t)
	for _, stream := range []bool{false, true} {
		scope := visualFixtureScope(t, g)
		response := officialVisualResponse("https://cdn.example/large-context-product.png")
		response["output"] = append([]object{{"type": "compaction", "id": "cmp-large", "encrypted_content": strings.Repeat("x", 3<<20)}}, response["output"].([]object)...)
		var data []byte
		if stream {
			encoded, _ := json.Marshal(object{"type": "response.completed", "response": response})
			data = append(append([]byte("data: "), encoded...), '\n', '\n')
		} else {
			data, _ = json.Marshal(response)
		}
		observer := &visualResponseObserver{scope: scope, stream: stream}
		for len(data) > 0 {
			length := min(len(data), 65536)
			observer.Write(data[:length])
			data = data[length:]
		}
		observer.finish()
		if len(scope.allowed) != 1 {
			t.Fatal("compaction response discarded real image-search results", stream)
		}
	}
}

func TestAgentLeasePreservesCompactionAndBillsProviderUsage(t *testing.T) {
	g := testGateway(t)
	g.cfg.reserveMicroUSD = 50000
	g.cfg.pricing = map[string]priceCard{"gpt-6.1-terra": {
		TextInput: 1000000, TextCached: 50000, Output: 5000000,
		Write30m: 1250000, Write12h: 2500000, LongContextThreshold: 272000,
	}}
	const opaque = "opaque+encrypted/state=="
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var fields object
		if err := json.NewDecoder(r.Body).Decode(&fields); err != nil {
			t.Error(err)
		}
		input, _ := fields["input"].([]any)
		if len(input) != 2 || objectString(asObject(input[0]), "encrypted_content") != opaque || objectString(asObject(input[1]), "type") != "configuration_update" {
			t.Error("opaque compaction/configuration input was modified", fields["input"])
		}
		if fields["max_output_tokens"] != float64(128000) || objectString(asObject(input[0]), "type") != "compaction" {
			t.Error("SDK model budget/compaction marker was modified")
		}
		management, _ := fields["context_management"].([]any)
		if len(management) != 1 || asObject(management[0])["compact_threshold"] != float64(829800) {
			t.Error("server compaction threshold did not reach upstream")
		}
		writeJSON(w, 200, object{"model": "gpt-6.1-terra", "status": "completed", "output": []object{{"type": "compaction", "id": "cmp-fixture", "encrypted_content": opaque}},
			"usage": object{"input_tokens": 900000, "output_tokens": 90000, "total_tokens": 990000,
				"input_tokens_details": object{"cached_tokens": 400000}, "output_tokens_details": object{"reasoning_tokens": 88000}}})
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "compaction", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "compaction meter", Upstream: u.ID, Policy: keyPolicy{H5: 5000000}})
	selected, err := g.resolveRoute(t.Context(), secret, netip.MustParseAddr("192.0.2.18"), true)
	if err != nil {
		t.Fatal(err)
	}
	lease, revoke := g.newAgentLease(t.Context(), selected, "gpt-6.1-terra", "high", "advanced", false)
	defer revoke()
	mux := http.NewServeMux()
	g.registerProxy(mux)
	server := httptest.NewServer(mux)
	defer server.Close()
	for range 2 {
		fields := object{"model": "gpt-6.1-terra", "store": false, "reasoning": object{"effort": "high"}, "input": []object{
			{"type": "compaction", "id": "cmp-fixture", "encrypted_content": opaque},
			{"type": "configuration_update", "reasoning": object{"effort": "high"}},
		}}
		applyAgentModelPolicy(fields, "gpt-6.1-terra")
		encoded, _ := json.Marshal(fields)
		req, _ := http.NewRequest("POST", server.URL+"/internal/agent/v1/responses", bytes.NewReader(encoded))
		req.Header.Set("Authorization", "Bearer "+lease)
		req.Header.Set("Content-Type", "application/json")
		resp, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != 200 || !strings.Contains(string(body), opaque) {
			t.Fatal("compaction response failed to pass through", resp.StatusCode, string(body))
		}
	}
	var records, tokens, cost int64
	if err := g.store.db.QueryRow("SELECT COUNT(*),SUM(total_tokens),SUM(cost_micro) FROM requests WHERE key_id=?", k.ID).Scan(&records, &tokens, &cost); err != nil || records != 2 || tokens != 1980000 || cost != 3430000 {
		t.Fatal("large context/output usage lost cached/reasoning/long-context pricing", records, tokens, cost, err)
	}
}
