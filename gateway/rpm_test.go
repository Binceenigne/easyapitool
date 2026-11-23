package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"image"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func setTestRPM(t *testing.T, g *gateway, limit int) {
	t.Helper()
	proposed := *g.appModels()
	proposed.Tiers = slices.Clone(proposed.Tiers)
	for i := range proposed.Tiers {
		if proposed.Tiers[i].ID == proposed.defaultTierID() {
			proposed.Tiers[i].RPMLimit = perMinuteLimit(limit)
		}
	}
	if _, err := g.saveModels(t.Context(), proposed); err != nil {
		t.Fatal(err)
	}
}

func TestRPMRollingBoundaryConcurrencyAndUnlimited(t *testing.T) {
	g := testGateway(t)
	setTestRPM(t, g, 3)
	u := addUpstream(t, g, "rolling-rpm", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "rolling", Upstream: u.ID})
	selected := route{key: k, upstream: u}
	now := time.Now().UTC().Truncate(time.Second)
	for range 3 {
		if _, err := g.store.reserve(t.Context(), selected, now, 0); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := g.store.reserve(t.Context(), selected, now.Add(time.Minute-time.Millisecond), 0); rpmFailure(err) == nil {
		t.Fatal("rolling window admitted a fourth call before 60 seconds")
	}
	if _, err := g.store.reserve(t.Context(), selected, now.Add(time.Minute), 0); err != nil {
		t.Fatal("the exact 60-second boundary did not expire old calls")
	}
	setTestRPM(t, g, 0)
	for range 12 {
		if _, err := g.store.reserve(t.Context(), selected, now.Add(time.Minute), 0); err != nil {
			t.Fatal("RPM zero was not unlimited")
		}
	}
	setTestRPM(t, g, 7)
	other, _ := issue(t, g, accessKey{Name: "independent-key", Upstream: u.ID})
	var accepted, rejected atomic.Int32
	var group sync.WaitGroup
	for range 24 {
		group.Go(func() {
			_, err := g.store.reserve(t.Context(), route{key: other, upstream: u}, now, 0)
			if err == nil {
				accepted.Add(1)
			} else if rpmFailure(err) != nil {
				rejected.Add(1)
			} else {
				t.Error(err)
			}
		})
	}
	group.Wait()
	if accepted.Load() != 7 || rejected.Load() != 17 {
		t.Fatalf("concurrent admission exceeded RPM: accepted=%d rejected=%d", accepted.Load(), rejected.Load())
	}
	var count int
	g.store.db.QueryRow("SELECT COUNT(*) FROM requests WHERE key_id=?", other.ID).Scan(&count)
	if count != 7 {
		t.Fatal("rejected admissions inserted extra billing reservations")
	}
}

func TestRPMRawManagedAndMetadataShareExactlyOneAdmission(t *testing.T) {
	g := testGateway(t)
	setTestRPM(t, g, 3)
	g.cfg.reserveMicroUSD = 100
	g.cfg.pricing = map[string]priceCard{"arbitrary-language": {Output: 1}, "gpt-image-2.6-flare": {Output: 1}}
	var raster bytes.Buffer
	png.Encode(&raster, image.NewNRGBA(image.Rect(0, 0, 8, 8)))
	var modelCalls, metadata atomic.Int32
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodGet {
			metadata.Add(1)
			io.WriteString(w, `{"data":[{"id":"arbitrary-language"}]}`)
			return
		}
		modelCalls.Add(1)
		io.Copy(io.Discard, r.Body)
		if r.URL.Path == "/v1/responses" {
			io.WriteString(w, `{"status":"completed","output_text":"render this","usage":{"input_tokens":0,"output_tokens":0,"total_tokens":0}}`)
		} else {
			json.NewEncoder(w).Encode(object{"data": []object{{"b64_json": base64.StdEncoding.EncodeToString(raster.Bytes())}},
				"usage": object{"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}})
		}
	}))
	defer provider.Close()
	g.client = provider.Client()
	u := addUpstream(t, g, "mixed-rpm", "", provider.URL+"/v1")
	k, secret := issue(t, g, accessKey{Name: "mixed", Upstream: u.ID, Policy: keyPolicy{Total: 10_000}})
	mux := http.NewServeMux()
	g.registerProxy(mux)
	request := func(method, path, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+secret)
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, r)
		return w
	}
	for range 3 {
		if w := request("GET", "/v1/models", ""); w.Code != http.StatusOK {
			t.Fatal("metadata models request failed")
		}
		if w := request("GET", "/v1/usage", ""); w.Code != http.StatusOK {
			t.Fatal("usage request failed")
		}
	}
	if w := request("POST", "/v1/responses", `{"model":"arbitrary-language","input":"one"}`); w.Code != http.StatusOK {
		t.Fatalf("raw response: %d %s", w.Code, w.Body.String())
	}
	selected := route{key: k, upstream: u}
	if _, _, _, err := g.rewrite(t.Context(), selected, "arbitrary-language", "medium", "two", false); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(t.Context())
	m := newMobile(ctx, g, t.TempDir())
	defer func() { cancel(); m.workers.Wait() }()
	key := keyInput{KeyID: k.ID, Value: secret, BaseURL: g.cfg.publicBase}
	m.keys[credentialID{"owner", k.ID}] = &mobileKey{keyInput: key}
	o, err := g.imageOptions(object{"stream": false, "quality": "medium"}, false, g.appModels())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := m.renderImage(ctx, credentialID{"owner", "render"}, key, selected.clientIP, "three", o, nil, 0); err != nil {
		t.Fatal(err)
	}
	limited := request("POST", "/v1/images/generations", `{"model":"gpt-image-2.6-flare","prompt":"blocked"}`)
	if limited.Code != http.StatusTooManyRequests || limited.Header().Get("Retry-After") == "" || !strings.Contains(limited.Body.String(), "rpm_limit_exceeded") {
		t.Fatalf("shared RPM did not return a structured 429: %d %s", limited.Code, limited.Body.String())
	}
	polish := httptest.NewRecorder()
	m.polish(polish, httptest.NewRequest("POST", "/api/v1/prompts/polish", strings.NewReader(`{"keyId":"`+k.ID+`","prompt":"four"}`)), "owner")
	if polish.Code != http.StatusTooManyRequests || polish.Header().Get("Retry-After") == "" {
		t.Fatal("managed polish swallowed RPM as a generic model/network error")
	}
	create := httptest.NewRecorder()
	m.createGeneration(create, httptest.NewRequest("POST", "/api/v1/image-generations", strings.NewReader(`{"keyId":"`+k.ID+`","prompt":"four","options":{"reasoningMode":"instant"}}`)), "owner")
	if create.Code != http.StatusTooManyRequests {
		t.Fatal("managed task creation did not reject an exhausted RPM window")
	}
	repeated := generationInput{KeyID: k.ID, Prompt: "existing", Options: object{"requestId": "idempotent", "reasoningMode": "instant"}}
	body, _ := json.Marshal(repeated)
	m.tasks[credentialID{"owner", "idempotent"}] = &task{fingerprint: sha256.Sum256(body)}
	duplicate := httptest.NewRecorder()
	m.createGeneration(duplicate, httptest.NewRequest("POST", "/api/v1/image-generations", bytes.NewReader(body)), "owner")
	if duplicate.Code != http.StatusOK {
		t.Fatal("repeating an existing task was treated as a new RPM admission")
	}
	usage := request("GET", "/v1/usage", "")
	var payload object
	json.Unmarshal(usage.Body.Bytes(), &payload)
	if rate := asObject(payload["rateLimit"]); rate["used"] != float64(3) || rate["remaining"] != float64(0) || rate["shared"] != true {
		t.Fatal("usage omitted the shared RPM state")
	}
	var rows, unsettled int
	var cost int64
	g.store.db.QueryRow("SELECT COUNT(*),COALESCE(SUM(cost_micro),0),SUM(CASE WHEN cost_micro IS NULL THEN 1 ELSE 0 END) FROM requests WHERE key_id=?", k.ID).Scan(&rows, &cost, &unsettled)
	if rows != 3 || modelCalls.Load() != 3 || metadata.Load() != 3 || cost != 0 || unsettled != 0 {
		t.Fatalf("duplicate admissions, metadata counting or leaked reservations: rows=%d calls=%d metadata=%d cost=%d unsettled=%d", rows, modelCalls.Load(), metadata.Load(), cost, unsettled)
	}
	// A planner can use the last available slot of an already accepted async
	// task. Preserve the subsequent image rejection as a typed 429 terminal result.
	setTestRPM(t, g, 1)
	asyncKey, asyncSecret := issue(t, g, accessKey{Name: "async", Upstream: u.ID})
	asyncInput := keyInput{KeyID: asyncKey.ID, Value: asyncSecret, BaseURL: g.cfg.publicBase}
	asyncID := credentialID{"owner", "async-limit"}
	jobCtx, jobCancel := context.WithCancel(ctx)
	m.tasks[asyncID] = &task{cancel: jobCancel}
	profile, err := g.modelsForKey(asyncKey)
	if err != nil {
		t.Fatal(err)
	}
	m.workers.Add(1)
	m.runGeneration(jobCtx, jobCancel, asyncID, asyncInput, selected.clientIP,
		generationInput{Prompt: "async", Options: object{"reasoningMode": "flash"}}, o, nil, profile)
	terminal := asObject(m.tasks[asyncID].lastEvent["result"])
	if terminal["status"] != 429 || terminal["errorCode"] != "rpm_limit_exceeded" || terminal["retryAfter"] == nil {
		t.Fatalf("async RPM error lost its status/code: %v", terminal)
	}
	g.store.db.QueryRow("SELECT COUNT(*) FROM requests WHERE key_id=?", asyncKey.ID).Scan(&rows)
	if rows != 1 || modelCalls.Load() != 4 {
		t.Fatal("async rejection dispatched or reserved an extra image request")
	}
}

func TestRPMPersistsAcrossFeeResetTierMigrationAndRestart(t *testing.T) {
	g := testGateway(t)
	setTestRPM(t, g, 1)
	other := limitedTier()
	other.RPMLimit = 1
	installTier(t, g, other)
	u := addUpstream(t, g, "rpm-persistence", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "persistent", Upstream: u.ID, Policy: keyPolicy{Total: 10_000}})
	now := time.Now()
	selected := route{key: k, upstream: u}
	id, err := g.store.reserve(t.Context(), selected, now, 100)
	if err != nil {
		t.Fatal(err)
	}
	cost := int64(100)
	g.store.finish(t.Context(), id, 200, nil, &cost)
	if _, err := g.store.changeKey(t.Context(), k.ID, nil, "all", nil, now); err != nil {
		t.Fatal(err)
	}
	if _, err := g.store.reserve(t.Context(), selected, now, 100); rpmFailure(err) == nil {
		t.Fatal("resetting USD usage reset RPM")
	}
	if _, err := g.deleteTier(t.Context(), "default", other.ID, g.appModels().Revision); err != nil {
		t.Fatal(err)
	}
	if _, err := g.store.reserve(t.Context(), selected, now, 100); rpmFailure(err) == nil {
		t.Fatal("tier migration reset the shared counter")
	}
	var sequence int
	var name, path string
	g.store.db.QueryRow("PRAGMA database_list").Scan(&sequence, &name, &path)
	g.store.db.Close()
	reopened, err := openStore(path, g.cfg.token)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.db.Close()
	if _, err := reopened.reserve(t.Context(), selected, now, 100); rpmFailure(err) == nil {
		t.Fatal("process/database restart reset RPM")
	}
	if _, err := reopened.reserve(t.Context(), selected, now.Add(time.Minute), 100); err != nil {
		t.Fatal("persisted RPM did not expire after 60 seconds")
	}
}

func TestRPMValidationAndImmediateLimitReduction(t *testing.T) {
	g := testGateway(t)
	setTestRPM(t, g, 0)
	u := addUpstream(t, g, "rpm-reduction", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "reduction", Upstream: u.ID})
	selected := route{key: k, upstream: u}
	now := time.Now()
	for i := range 4 {
		if _, err := g.store.reserve(t.Context(), selected, now.Add(time.Duration(i)*time.Second), 0); err != nil {
			t.Fatal(err)
		}
	}
	setTestRPM(t, g, 2)
	_, err := g.store.reserve(t.Context(), selected, now.Add(4*time.Second), 0)
	var limited *rpmError
	if !errors.As(err, &limited) || limited.state.used != 4 || !limited.state.resetAt.Equal(time.UnixMilli(now.Add(2*time.Second).UnixMilli()).Add(time.Minute)) {
		t.Fatal("new lower limit did not apply immediately or gave the wrong retry boundary")
	}
	client := newTierAdminTestClient(t, g)
	for _, value := range []string{`-1`, `1000001`, `1.5`, `"3"`, `null`, `true`, `9223372036854775808`} {
		data, _ := json.Marshal(fullTier())
		var tier map[string]json.RawMessage
		json.Unmarshal(data, &tier)
		tier["rpmLimit"] = json.RawMessage(value)
		if w := client.request(t, "POST", "/admin/tiers", object{"tier": tier, "revision": g.appModels().Revision}); w.Code != http.StatusBadRequest {
			t.Fatalf("invalid RPM accepted: %s status=%d", value, w.Code)
		}
	}
}

func TestRPMEarlierConcurrentArrivalCannotSkipANewerCommittedRequest(t *testing.T) {
	g := testGateway(t)
	setTestRPM(t, g, 1)
	u := addUpstream(t, g, "arrival-order", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "inverted", Upstream: u.ID})
	selected := route{key: k, upstream: u}
	before := time.Now()
	if _, err := g.store.reserve(t.Context(), selected, before.Add(time.Millisecond), 0); err != nil {
		t.Fatal(err)
	}
	if _, err := g.store.reserve(t.Context(), selected, before, 0); rpmFailure(err) == nil {
		t.Fatal("an older arrival timestamp bypassed a newer admission")
	}
}

func TestRPMAdmissionClockRunsAfterTheWriterIsAcquired(t *testing.T) {
	g := testGateway(t)
	setTestRPM(t, g, 1)
	u := addUpstream(t, g, "admission-clock", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "clock", Upstream: u.ID})
	blocker, err := g.store.db.BeginTx(t.Context(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback()
	var called atomic.Bool
	started := make(chan struct{})
	finished := make(chan error, 1)
	admission := time.Now().Add(time.Minute)
	go func() {
		close(started)
		_, err := g.store.reserveWithClock(t.Context(), route{key: k, upstream: u}, func() time.Time {
			called.Store(true)
			return admission
		}, 0)
		finished <- err
	}()
	<-started
	if called.Load() {
		t.Fatal("admission timestamp was captured before acquiring the writer")
	}
	blocker.Rollback()
	if err := <-finished; err != nil {
		t.Fatal(err)
	}
	var created int64
	g.store.db.QueryRow("SELECT created FROM requests WHERE key_id=?", k.ID).Scan(&created)
	if !called.Load() || created != admission.UnixMilli() {
		t.Fatal("ledger did not use the actual admission clock")
	}
}
