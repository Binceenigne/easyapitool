package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"sync/atomic"
	"testing"
)

func TestInstantContinuationPlansOnceAndSendsExistingReferenceToImageEdit(t *testing.T) {
	g := testGateway(t)
	encoded := retryPNG(t)
	picture, _ := base64.StdEncoding.DecodeString(encoded)
	var planners, edits atomic.Int32
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/responses" {
			planners.Add(1)
			writeJSON(w, 200, object{"model": "gpt-6-luna", "usage": object{"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
			return
		}
		if r.URL.Path != "/v1/images/edits" {
			t.Error("continuation became new generation", r.URL.Path)
			w.WriteHeader(400)
			return
		}
		edits.Add(1)
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Error(err)
			return
		}
		file, _, err := r.FormFile("image[]")
		if err != nil {
			t.Error("existing reference missing", err)
			return
		}
		defer file.Close()
		data, _ := io.ReadAll(file)
		if !bytes.Equal(data, picture) || r.FormValue("prompt") != "preserve the subject, change jacket to blue" {
			t.Error("reference or planned edit prompt changed")
		}
		writeJSON(w, 200, object{"data": []object{{"b64_json": encoded}}})
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "continuation", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "continuation", Upstream: u.ID})
	mux := http.NewServeMux()
	g.registerProxy(mux)
	proxy := httptest.NewServer(mux)
	defer proxy.Close()
	g.cfg.agentProxyBase = proxy.URL + "/internal/agent/v1"
	g.cfg.agentWorkerToken = strings.Repeat("q", 48)
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var envelope struct {
			Request object `json:"request"`
		}
		_ = json.NewDecoder(r.Body).Decode(&envelope)
		request := envelope.Request
		if objectString(request, "mode") != "continuation" || !strings.Contains(objectString(request, "instructions"), "original red jacket") || objectString(request, "input") != "change jacket to blue" {
			t.Error("hidden continuation planner lost the parent or current edit context", request)
		}
		call, _ := http.NewRequest("POST", objectString(request, "base_url")+"/responses", strings.NewReader(agentTestBody(objectString(request, "model"), objectString(request, "effort"))))
		call.Header.Set("Authorization", "Bearer "+objectString(request, "api_key"))
		call.Header.Set("Content-Type", "application/json")
		response, err := proxy.Client().Do(call)
		if err != nil {
			t.Error(err)
			return
		}
		response.Body.Close()
		if response.StatusCode != 200 {
			t.Error("continuation planner denied", response.StatusCode)
		}
		io.WriteString(w, "{\"type\":\"result\",\"text\":\"preserve the subject, change jacket to blue\"}\n")
	}))
	defer worker.Close()
	g.cfg.agentWorkerURL = worker.URL
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	id := credentialID{"owner", "continue"}
	parent, err := m.saveAsset(id.owner, bytes.NewReader(picture), false, false)
	if err != nil {
		t.Fatal(err)
	}
	parent.holds = 1
	m.assets[parent.id] = parent
	jobCtx, jobCancel := context.WithCancel(ctx)
	m.tasks[id] = &task{cancel: jobCancel}
	profile, err := g.modelsForKey(k)
	if err != nil {
		t.Fatal(err)
	}
	options, err := g.imageOptions(object{"stream": false}, true, profile)
	if err != nil {
		t.Fatal(err)
	}
	input := generationInput{Prompt: "change jacket to blue", ReferenceIDs: []string{parent.id}, Options: object{"reasoningMode": "instant", "continuation": true, "parentSetId": "existing-set", "previousPrompt": "original red jacket"}}
	m.workers.Add(1)
	m.runGeneration(jobCtx, jobCancel, id, keyInput{KeyID: k.ID, Value: secret, BaseURL: g.cfg.publicBase}, netip.MustParseAddr("192.0.2.3"), input, options, []asset{parent}, profile)
	if planners.Load() != 1 || edits.Load() != 1 || asObject(m.tasks[id].lastEvent["result"])["ok"] != true {
		t.Fatal("continuation did not use one SDK planner plus one reference edit", m.tasks[id].lastEvent)
	}
}
