package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/netip"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

type event struct {
	sequence int64
	owner    string
	data     object
}

type task struct {
	cancel      context.CancelFunc
	lastEvent   object
	lastChanged time.Time
	fingerprint [32]byte
}

type generationInput struct {
	KeyID        string
	Prompt       string
	ReferenceIDs []string
	Options      object
}

const visualSkillSeparator = "\n\n【已选视觉风格】\n"

func (req generationInput) originalPrompt() string {
	original := strings.TrimSpace(objectString(req.Options, "originalPrompt"))
	if original != "" && strings.HasPrefix(req.Prompt, original+visualSkillSeparator) {
		return original
	}
	return req.Prompt
}

func (m *mobile) createGeneration(w http.ResponseWriter, r *http.Request, owner string) {
	var req generationInput
	if !decodeRequest(w, r, 256<<10, &req) {
		return
	}
	req.Prompt = strings.TrimSpace(req.Prompt)
	if req.Prompt == "" || utf8.RuneCountInString(req.Prompt) > 32000 || len(req.ReferenceIDs) > 16 {
		jsonError(w, http.StatusBadRequest, "请填写提示词并提供至多 16 张参考图")
		return
	}
	id := objectString(req.Options, "requestId")
	if len(id) > 80 || strings.TrimSpace(id) != id || strings.ContainsAny(id, "/\\?#") {
		jsonError(w, http.StatusBadRequest, "任务编号无效")
		return
	}
	if id == "" {
		id = randomID(18)
	}
	key, err := m.getKey(owner, req.KeyID)
	if err != nil {
		jsonError(w, http.StatusNotFound, err.Error())
		return
	}
	var settings *modelSettings
	var selected route
	if m.g.managedKey(key) {
		selected, err = m.resolveKey(r.Context(), key, m.g.clientIP(r), false)
		if err != nil {
			m.g.proxyError(w, err)
			return
		}
		settings, err = m.g.modelsForKey(selected.key)
		if err != nil {
			m.g.proxyError(w, err)
			return
		}
	}
	if original := req.originalPrompt(); original != req.Prompt {
		if settings != nil && !settings.Tier.visualSkillsEnabled() {
			m.g.proxyError(w, denied("视觉风格"))
			return
		}
		mode := objectString(req.Options, "reasoningMode")
		if (mode == "" || mode == "instant") && strings.Contains(req.Prompt[len(original)+len(visualSkillSeparator):], "\nAgent 规划：") {
			jsonError(w, http.StatusBadRequest, "此视觉风格需要使用已开放的思维模式")
			return
		}
	}
	options, err := m.g.imageOptions(req.Options, len(req.ReferenceIDs) > 0, settings)
	if err != nil {
		jsonError(w, http.StatusBadRequest, err.Error())
		return
	}
	if settings != nil {
		if err := m.g.checkImageTier(selected.key, options, len(req.ReferenceIDs)); err != nil {
			m.g.proxyError(w, err)
			return
		}
	}
	ip := m.g.clientIP(r)
	payload, _ := json.Marshal(req)
	fingerprint := sha256.Sum256(payload)
	jobID := credentialID{owner, id}
	m.mu.Lock()
	existing := m.tasks[jobID]
	m.mu.Unlock()
	if existing != nil {
		if existing.fingerprint != fingerprint {
			jsonError(w, http.StatusConflict, "任务编号已被使用")
		} else {
			writeJSON(w, http.StatusOK, object{"ok": true, "requestId": id, "status": "queued"})
		}
		return
	}
	if settings != nil {
		if err := m.g.checkRPM(r.Context(), selected); err != nil {
			m.g.proxyError(w, err)
			return
		}
	}
	ctx, cancel := context.WithCancel(m.ctx)
	m.mu.Lock()
	if existing := m.tasks[jobID]; existing != nil {
		m.mu.Unlock()
		cancel()
		if existing.fingerprint != fingerprint {
			jsonError(w, http.StatusConflict, "任务编号已被使用")
			return
		}
		writeJSON(w, http.StatusOK, object{"ok": true, "requestId": id, "status": "queued"})
		return
	}
	active, sameOwner := 0, 0
	for indexed, job := range m.tasks {
		if job.cancel != nil {
			active++
			if indexed.owner == owner {
				sameOwner++
			}
		}
	}
	if m.ctx.Err() != nil || active >= 24 || sameOwner >= 4 || len(m.tasks) >= 2000 {
		m.mu.Unlock()
		cancel()
		jsonError(w, http.StatusTooManyRequests, "生成队列已满，请稍后再试")
		return
	}
	refs := make([]asset, 0, len(req.ReferenceIDs))
	seen := map[string]bool{}
	for _, refID := range req.ReferenceIDs {
		if seen[refID] {
			continue
		}
		ref, ok := m.assets[refID]
		if !ok || ref.owner != owner || time.Now().After(ref.expires) || !oneOf(ref.format, "png", "jpeg", "webp") {
			m.mu.Unlock()
			cancel()
			jsonError(w, http.StatusNotFound, "参考图片不存在或已过期")
			return
		}
		refs = append(refs, ref)
		seen[refID] = true
	}
	for _, ref := range refs {
		ref.holds++
		m.assets[ref.id] = ref
	}
	m.tasks[jobID] = &task{cancel: cancel, fingerprint: fingerprint}
	m.publishLocked(jobID, object{"type": "task_created"}, false)
	m.workers.Add(1)
	m.mu.Unlock()
	go m.runGeneration(ctx, cancel, jobID, key, ip, req, options, refs, settings)
	writeJSON(w, http.StatusOK, object{"ok": true, "requestId": id, "status": "queued"})
}

func (m *mobile) publish(id credentialID, data object, terminal bool) {
	m.mu.Lock()
	accepted := m.tasks[id] != nil && m.tasks[id].cancel != nil
	m.publishLocked(id, data, terminal)
	m.mu.Unlock()
	if terminal && accepted {
		m.persistTerminal(id, data)
	}
}

func (m *mobile) publishLocked(id credentialID, data object, terminal bool) {
	job := m.tasks[id]
	if job == nil || job.cancel == nil {
		return
	}
	data["requestId"] = id.id
	job.lastEvent, job.lastChanged = data, time.Now()
	if terminal {
		job.cancel = nil
	}
	m.emitLocked(id.owner, data)
}

func (m *mobile) emitLocked(owner string, data object) {
	m.sequence++
	data["eventId"] = m.sequence
	if len(m.events) >= 2048 {
		copy(m.events, m.events[512:])
		clear(m.events[len(m.events)-512:])
		m.events = m.events[:len(m.events)-512]
	}
	m.events = append(m.events, event{m.sequence, owner, data})
	close(m.changed)
	m.changed = make(chan struct{})
}

func (m *mobile) taskState(w http.ResponseWriter, r *http.Request, owner string) {
	m.mu.Lock()
	var data object
	if task := m.tasks[credentialID{owner, r.PathValue("id")}]; task != nil {
		data = task.lastEvent
	}
	m.mu.Unlock()
	if data == nil {
		data, _ = m.g.store.terminalEvent(r.Context(), credentialID{owner, r.PathValue("id")})
	}
	if data == nil {
		jsonError(w, http.StatusNotFound, "任务不存在")
		return
	}
	writeJSON(w, http.StatusOK, object{"ok": true, "event": data})
}

func (m *mobile) cancelTask(w http.ResponseWriter, r *http.Request, owner string) {
	id := credentialID{owner, r.PathValue("id")}
	m.mu.Lock()
	job := m.tasks[id]
	ok := job != nil && job.cancel != nil
	if ok {
		job.cancel()
		m.publishLocked(id, object{"type": "task_cancel_requested"}, false)
	}
	m.mu.Unlock()
	if !ok {
		writeJSON(w, http.StatusOK, object{"ok": false, "error": "任务不存在或已经结束"})
		return
	}
	writeJSON(w, http.StatusOK, object{"ok": true, "requestId": id.id})
}

func (m *mobile) streamEvents(w http.ResponseWriter, r *http.Request, owner string) {
	cursor, _ := strconv.ParseInt(r.URL.Query().Get("cursor"), 10, 64)
	m.mu.Lock()
	if cursor > m.sequence || cursor < 0 {
		cursor = 0
	}
	m.mu.Unlock()
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache, no-transform")
	w.Header().Set("X-Accel-Buffering", "no")
	w.Header().Set("X-Event-Epoch", m.epoch)
	w.WriteHeader(http.StatusOK)
	controller := http.NewResponseController(w)
	if controller.Flush() != nil {
		return
	}
	keepAlive := time.NewTicker(15 * time.Second)
	defer keepAlive.Stop()
	for {
		m.mu.Lock()
		events := []event{}
		for _, e := range m.events {
			if e.sequence > cursor && owner == e.owner {
				events = append(events, e)
			}
		}
		cursor = m.sequence
		changed := m.changed
		m.mu.Unlock()
		for _, e := range events {
			payload, err := json.Marshal(e.data)
			if err != nil {
				return
			}
			if _, err := fmt.Fprintf(w, "id: %d\ndata: %s\n\n", e.sequence, payload); err != nil || controller.Flush() != nil {
				return
			}
		}
		select {
		case <-changed:
		case <-r.Context().Done():
			return
		case <-m.ctx.Done():
			return
		case <-keepAlive.C:
			if _, err := fmt.Fprint(w, ": keep-alive\n\n"); err != nil || controller.Flush() != nil {
				return
			}
		}
	}
}

func (m *mobile) runGeneration(ctx context.Context, cancel context.CancelFunc, id credentialID, key keyInput, ip netip.Addr, req generationInput, opts imageOptions, refs []asset, settings *modelSettings) {
	defer m.workers.Done()
	defer cancel()
	heldReferences := append([]asset(nil), refs...)
	defer func() {
		m.mu.Lock()
		for _, ref := range heldReferences {
			a := m.assets[ref.id]
			a.holds--
			m.assets[ref.id] = a
		}
		m.mu.Unlock()
	}()
	visual := newAgentVisualScope(m, id, opts, len(refs))
	visual.initial = append([]asset(nil), refs...)
	workspaceLimit := 16
	if settings != nil && settings.Tier != nil {
		workspaceLimit = settings.Tier.MaxReferences
	}
	visual.workspace = visual.newWorkspace(ctx, req.Options, workspaceLimit)
	defer visual.release()
	ctx = context.WithValue(ctx, agentVisualScopeKey{}, visual)
	result := object{"ok": false, "requestId": id.id, "setId": id.id, "sessionId": id.id,
		"prompt": req.Prompt, "originalPrompt": req.originalPrompt(), "imageModel": opts.model,
		"requestedCount": opts.n, "aspectRatio": opts.ratio, "transparency": opts.background,
		"createdAt": time.Now().UTC(), "items": []object{}, "reasoningMode": objectString(req.Options, "reasoningMode")}
	result["inputAssetIds"] = []string{}
	for _, ref := range refs {
		result["inputAssetIds"] = append(result["inputAssetIds"].([]string), ref.id)
	}
	result["parentSetId"], result["roundNumber"] = objectString(req.Options, "parentSetId"), max(1, numberInt(req.Options["roundNumber"]))
	defer func() {
		result["reasoningStats"], result["reasoningTurns"], result["webSearchSources"] = visual.stats, visual.turns, visual.sources
		for name, value := range visual.referenceMetadata() {
			result[name] = value
		}
		if visual.workspace != nil {
			result["workspaceId"], result["sessionId"], result["parentSetId"], result["continuationPlan"] = visual.workspace.id, visual.workspace.id, visual.workspace.parent, visual.workspace.plan()
			result["operation"] = visual.workspace.operation
		}
		for name, value := range visual.planning {
			result[name] = value
		}
		if ctx.Err() != nil {
			result["cancelled"] = true
			result["error"] = "生成已取消"
			if details := asObject(result["errorDetails"]); details == nil {
				attachOperationFailure(result, operationError("image", ctx.Err(), 0, attemptLimit(m.g.cfg.imageAttempts)))
			}
		}
		event := object{"type": "task_result", "result": result}
		if key.BaseURL == m.g.cfg.publicBase {
			query, cancel := context.WithTimeout(m.ctx, 3*time.Second)
			if selected, err := m.resolveKey(query, key, ip, false); err == nil {
				if snapshot, err := m.g.store.usage(query, selected.key, time.Now()); err == nil {
					event["quota"] = object{"keyId": key.KeyID, "quota": snapshot["quota"], "windows": snapshot["windows"], "revision": snapshot["quotaRevision"], "asOf": snapshot["asOf"]}
				}
			}
			cancel()
		}
		m.publish(id, event, true)
	}()
	select {
	case m.capacity <- struct{}{}:
		defer func() { <-m.capacity }()
	case <-ctx.Done():
		return
	}
	m.publish(id, object{"type": "set_started", "setId": id.id, "requestedCount": opts.n, "prompt": req.Prompt, "imageModel": opts.model}, false)
	prompt := req.Prompt
	mode := objectString(req.Options, "reasoningMode")
	if isContinuation(req.Options) || mode != "" && (mode != "instant" || settings != nil && settings.ReasoningModes[mode].Model != "") {
		var usage object
		var err error
		prompt, usage, err = m.preparePrompt(ctx, id, key, ip, req, settings)
		if err != nil {
			result["reasoningStatus"] = "failed"
			err = operationError("agent", err, 0, attemptLimit(m.g.cfg.agentAttempts))
			result["error"] = err.Error()
			attachRPMFailure(result, err)
			attachOperationFailure(result, err)
			failed := failedItems(opts.n, err.Error())
			for _, item := range failed {
				attachOperationFailure(item, err)
			}
			result["items"] = failed
			return
		}
		result["prompt"], result["reasoningUsage"] = prompt, usage
		if visual.workspace != nil {
			refs = visual.workspace.selectedAssets()
		}
		result["inputAssetIds"] = []string{}
		for _, ref := range refs {
			result["inputAssetIds"] = append(result["inputAssetIds"].([]string), ref.id)
		}
		refs = append(refs, visual.adopted()...)
		result["webReferences"], result["webReferenceCount"] = visual.references(), len(visual.references())
		result["webSearchSources"], result["reasoningStats"], result["reasoningTurns"] = visual.sources, visual.stats, visual.turns
	}
	items := make([]object, 0, opts.n)
	for i := 0; i < opts.n && ctx.Err() == nil; i++ {
		m.publish(id, object{"type": "item_started", "itemIndex": i}, false)
		item, err := m.renderImageWithRetry(ctx, id, key, ip, prompt, opts, refs, i)
		if err != nil {
			item = object{"ok": false, "itemIndex": i, "error": err.Error()}
			result["error"] = err.Error()
			attachRPMFailure(result, err)
			attachRPMFailure(item, err)
			attachOperationFailure(result, err)
			attachOperationFailure(item, err)
			failure := object{"type": "item_failed", "itemIndex": i, "error": err.Error()}
			attachRPMFailure(failure, err)
			attachOperationFailure(failure, err)
			m.publish(id, failure, false)
		} else {
			result["ok"] = true
			m.publish(id, object{"type": "item_completed", "itemIndex": i, "result": item}, false)
		}
		items = append(items, item)
	}
	result["items"] = items
}

func failedItems(n int, message string) []object {
	items := make([]object, n)
	for i := range items {
		items[i] = object{"ok": false, "itemIndex": i, "status": "failed", "error": message}
	}
	return items
}
