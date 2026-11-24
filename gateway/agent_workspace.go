package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"image"
	"image/png"
	"io"
	"net/http"
	"os"
	"slices"
	"sort"
	"sync"
	"time"
)

// A workspace grants access to registered assets from this owner's actual
// parent history, never a client supplied filesystem path or another device.
type agentWorkspace struct {
	mu                               sync.Mutex
	scope                            *agentVisualScope
	id, parent, operation, rationale string
	history                          []object
	assets                           map[string]asset
	metadata                         map[string]object
	order, selected, required        []string
	viewed                           map[string]bool
	held                             map[string]bool
}

func (g *gateway) minimumContinuationPreset() modelPreset {
	model := g.appModels().Reasoning["luna"].Model
	if model == "" {
		model = g.cfg.reasoningPresets["luna"]
	}
	if model == "" {
		model = builtinReasoningModels["luna"]
	}
	return modelPreset{Model: model, Effort: supportedReasoningEffort(model, "low")}
}

func (s *agentVisualScope) newWorkspace(ctx context.Context, options object, maximum int) *agentWorkspace {
	w := &agentWorkspace{scope: s, id: s.id.id, parent: objectString(options, "parentSetId"), operation: "generate", assets: map[string]asset{}, metadata: map[string]object{}, viewed: map[string]bool{}, held: map[string]bool{}, selected: []string{}, required: []string{}}
	if w.parent == "" {
		return nil
	}
	seen := map[string]bool{}
	for next := w.parent; next != "" && !seen[next]; {
		seen[next] = true
		event, err := s.mobile.g.store.terminalEvent(ctx, credentialID{s.id.owner, next})
		if err != nil {
			s.mobile.mu.Lock()
			if task := s.mobile.tasks[credentialID{s.id.owner, next}]; task != nil {
				event = task.lastEvent
			}
			s.mobile.mu.Unlock()
		}
		result := asObject(event["result"])
		if result == nil {
			break
		}
		if len(w.history) == 0 {
			if id := objectString(result, "sessionId"); id != "" {
				w.id = id
			}
		}
		outputs := []string{}
		for _, item := range arrayObjects(result["items"]) {
			if id := objectString(item, "resourceId"); id != "" {
				outputs = append(outputs, id)
				w.register(id, "output", next)
			}
		}
		inputs := []string{}
		for _, raw := range arrayValues(result["inputAssetIds"]) {
			if id, ok := raw.(string); ok {
				inputs = append(inputs, id)
				w.register(id, "input", next)
			}
		}
		for _, name := range []string{"webReferences", "webInspectedReferences"} {
			for _, item := range arrayObjects(result[name]) {
				w.register(objectString(item, "resourceId"), name, next)
			}
		}
		w.history = append(w.history, object{"setId": next, "parentSetId": objectString(result, "parentSetId"), "roundNumber": result["roundNumber"], "userPrompt": objectString(result, "originalPrompt"), "reasoningSummary": objectString(result, "reasoningSummary"), "operation": objectString(result, "operation"), "inputAssetIds": inputs, "outputAssetIds": outputs})
		next = objectString(result, "parentSetId")
	}
	slices.Reverse(w.history)
	// Enumerate the owner's persisted terminal records in storage pages. The
	// page size limits each DB read, not the number of rounds in a workspace.
	for cursor := ""; ; {
		rows, err := s.mobile.g.store.db.QueryContext(ctx, `SELECT task_id,value FROM task_terminal_events WHERE owner=? AND task_id>? AND created>? ORDER BY task_id LIMIT 100`, s.id.owner, cursor, time.Now().Add(-7*24*time.Hour).UnixMilli())
		if err != nil {
			break
		}
		count := 0
		for rows.Next() {
			var id string
			var sealed []byte
			if rows.Scan(&id, &sealed) != nil {
				continue
			}
			cursor = id
			count++
			if seen[id] {
				continue
			}
			raw, err := s.mobile.g.store.unseal(sealed, "task-terminal:"+s.id.owner+":"+id)
			if err != nil {
				continue
			}
			var event object
			if json.Unmarshal(raw, &event) != nil {
				continue
			}
			result := asObject(event["result"])
			workspaceID := objectString(result, "workspaceId")
			if workspaceID == "" {
				workspaceID = objectString(result, "sessionId")
			}
			if workspaceID != w.id {
				continue
			}
			seen[id] = true
			w.addHistory(id, result)
		}
		rows.Close()
		if count < 100 || ctx.Err() != nil {
			break
		}
	}
	// Include already committed in-memory rounds if their asynchronous storage
	// write has not completed yet; never inspect an active sibling task.
	s.mobile.mu.Lock()
	pending := map[string]object{}
	for id, task := range s.mobile.tasks {
		if id.owner != s.id.owner || task.cancel != nil || seen[id.id] {
			continue
		}
		result := asObject(task.lastEvent["result"])
		workspaceID := objectString(result, "workspaceId")
		if workspaceID == "" {
			workspaceID = objectString(result, "sessionId")
		}
		if workspaceID == w.id {
			pending[id.id] = result
		}
	}
	s.mobile.mu.Unlock()
	for id, result := range pending {
		seen[id] = true
		w.addHistory(id, result)
	}
	sort.SliceStable(w.history, func(i, j int) bool {
		left, right := numberInt(w.history[i]["roundNumber"]), numberInt(w.history[j]["roundNumber"])
		if left != right {
			return left < right
		}
		return objectString(w.history[i], "setId") < objectString(w.history[j], "setId")
	})
	for _, round := range w.history {
		if objectString(round, "setId") != w.parent {
			continue
		}
		for _, raw := range arrayValues(round["outputAssetIds"]) {
			id, _ := raw.(string)
			candidate := w.assets[id]
			if candidate.id == "" || !oneOf(candidate.format, "png", "jpeg", "webp") || len(s.initial) >= maximum {
				continue
			}
			duplicate := false
			for _, current := range s.initial {
				if sameWorkspaceAsset(current, candidate) {
					duplicate = true
					break
				}
			}
			if !duplicate {
				s.initial = append(s.initial, candidate)
			}
		}
	}
	for _, reference := range s.initial {
		w.register(reference.id, "current-input", s.id.id)
		w.viewed[reference.id] = true
		w.selected = append(w.selected, reference.id)
		w.required = append(w.required, reference.id)
	}
	if len(w.selected) > 0 {
		w.operation = "edit"
	}
	return w
}

func sameWorkspaceAsset(left, right asset) bool {
	if left.id == right.id {
		return true
	}
	if left.size != right.size || left.format != right.format {
		return false
	}
	hash := func(path string) []byte {
		file, err := os.Open(path)
		if err != nil {
			return nil
		}
		defer file.Close()
		sum := sha256.New()
		if _, err := io.Copy(sum, file); err != nil {
			return nil
		}
		return sum.Sum(nil)
	}
	first, second := hash(left.path), hash(right.path)
	return first != nil && bytes.Equal(first, second)
}

func (w *agentWorkspace) addHistory(id string, result object) {
	outputs, inputs := []string{}, []string{}
	for _, item := range arrayObjects(result["items"]) {
		if assetID := objectString(item, "resourceId"); assetID != "" {
			outputs = append(outputs, assetID)
			w.register(assetID, "output", id)
		}
	}
	for _, raw := range arrayValues(result["inputAssetIds"]) {
		if assetID, ok := raw.(string); ok {
			inputs = append(inputs, assetID)
			w.register(assetID, "input", id)
		}
	}
	for _, name := range []string{"webReferences", "webInspectedReferences"} {
		for _, item := range arrayObjects(result[name]) {
			w.register(objectString(item, "resourceId"), name, id)
		}
	}
	w.history = append(w.history, object{"setId": id, "parentSetId": objectString(result, "parentSetId"), "roundNumber": result["roundNumber"], "userPrompt": objectString(result, "originalPrompt"), "reasoningSummary": objectString(result, "reasoningSummary"), "operation": objectString(result, "operation"), "inputAssetIds": inputs, "outputAssetIds": outputs})
}

func arrayValues(value any) []any {
	if items, ok := value.([]any); ok {
		return items
	}
	if items, ok := value.([]string); ok {
		out := []any{}
		for _, item := range items {
			out = append(out, item)
		}
		return out
	}
	return nil
}
func (w *agentWorkspace) register(id, role, setID string) {
	if id == "" {
		return
	}
	w.scope.mobile.mu.Lock()
	a, ok := w.scope.mobile.assets[id]
	w.scope.mobile.mu.Unlock()
	if !ok || a.owner != w.scope.id.owner || time.Now().After(a.expires) {
		return
	}
	if w.assets[id].id != "" {
		roles, _ := w.metadata[id]["sourceRoles"].([]string)
		if !slices.Contains(roles, role) {
			w.metadata[id]["sourceRoles"] = append(roles, role)
		}
		sets, _ := w.metadata[id]["sourceSetIds"].([]string)
		if !slices.Contains(sets, setID) {
			w.metadata[id]["sourceSetIds"] = append(sets, setID)
		}
		return
	}
	w.scope.mobile.mu.Lock()
	held := w.scope.mobile.assets[id]
	held.holds++
	w.scope.mobile.assets[id] = held
	w.scope.mobile.mu.Unlock()
	w.held[id] = true
	w.assets[id] = a
	w.order = append(w.order, id)
	w.metadata[id] = object{"assetId": id, "path": "workspace://" + id, "previewPath": "workspace://" + id, "sourceRoles": []string{role}, "sourceSetIds": []string{setID}, "description": "", "format": a.format, "exists": true}
}
func (w *agentWorkspace) limit(key accessKey) (int, error) {
	if key.ID == "" {
		return 16, nil
	}
	profile, err := w.scope.mobile.g.modelsForKey(key)
	if err != nil {
		return 0, err
	}
	if !profile.Tier.ImageEditing {
		return 0, denied("工作区图片分析")
	}
	return profile.Tier.MaxReferences, nil
}
func (w *agentWorkspace) list(key accessKey, kind string, offset, limit int) (object, error) {
	if !oneOf(kind, "history", "assets") || offset < 0 || limit < 1 || limit > 50 {
		return nil, errors.New("工作区分页参数无效")
	}
	maximum, err := w.limit(key)
	if err != nil {
		return nil, err
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	items := []object{}
	if kind == "history" {
		items = append(items, w.history...)
	} else {
		for _, id := range w.order {
			item := object{}
			for k, v := range w.metadata[id] {
				item[k] = v
			}
			item["viewed"] = w.viewed[id]
			if _, err := os.Stat(w.assets[id].path); err != nil {
				item["exists"] = false
			}
			items = append(items, item)
		}
	}
	total := len(items)
	start, end := min(offset, total), min(offset+limit, total)
	bytes := 0
	for index := start; index < end; index++ {
		encoded, _ := json.Marshal(items[index])
		if index > start && bytes+len(encoded) > 1<<20 {
			end = index
			break
		}
		bytes += len(encoded)
	}
	return object{"workspaceId": w.id, "parentSetId": w.parent, "kind": kind, "offset": offset, "total": total, "hasMore": end < total, "items": items[start:end], "resultCount": end - start, "selectedAssetIds": slices.Clone(w.selected), "operation": w.operation, "maxReferences": maximum, "remainingReferenceSlots": max(0, maximum-len(w.selected))}, nil
}
func (w *agentWorkspace) view(key accessKey, ids []string) (object, error) {
	maximum, err := w.limit(key)
	if err != nil {
		return nil, err
	}
	if len(ids) < 1 || len(ids) > 16 {
		return nil, errors.New("请提供工作区素材ID")
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	assets, failures := []object{}, []object{}
	for _, id := range ids {
		a, ok := w.assets[id]
		if !ok {
			return nil, errors.New("素材不属于当前工作区")
		}
		item := object{}
		for k, v := range w.metadata[id] {
			item[k] = v
		}
		if !w.viewed[id] {
			w.scope.mu.Lock()
			webViews := len(w.scope.inspected)
			w.scope.mu.Unlock()
			if len(w.viewed)+webViews >= maximum {
				failures = append(failures, object{"assetId": id, "message": "当前档位的图片分析槽位已满"})
				continue
			}
			referenceScope := &agentVisualScope{id: w.scope.id, initial: []asset{a}}
			input, err := referenceScope.initialInput("")
			if err != nil {
				failures = append(failures, object{"assetId": id, "message": err.Error()})
				continue
			}
			parts := arrayObjects(arrayObjects(input)[0]["content"])
			item["previewDataUrl"] = objectString(parts[1], "image_url")
			w.viewed[id] = true
		}
		item["viewed"] = true
		assets = append(assets, item)
	}
	return object{"assets": assets, "errors": failures, "resultCount": len(assets)}, nil
}
func (w *agentWorkspace) selectInputs(key accessKey, operation string, ids []string, rationale string) (object, error) {
	maximum, err := w.limit(key)
	if err != nil {
		return nil, err
	}
	if !oneOf(operation, "edit", "generate") || rationale == "" || len(rationale) > 6400 {
		return nil, errors.New("工作区选择无效")
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	selected := []string{}
	for _, id := range ids {
		if w.assets[id].id == "" {
			return nil, errors.New("只能选择本工作区已注册的素材")
		}
		if _, err := os.Stat(w.assets[id].path); err != nil {
			return nil, errors.New("工作区素材文件已不可用")
		}
		if w.assets[id].format == "svg" {
			return nil, errors.New("此SVG原图需要可用的位图预览才能作为图片接口的参考输入")
		}
		if w.assets[id].format == "gif" {
			file, err := os.Open(w.assets[id].path)
			if err != nil {
				return nil, err
			}
			decoded, _, err := image.Decode(file)
			file.Close()
			if err != nil {
				return nil, errors.New("GIF参考图无法读取")
			}
			var raster bytes.Buffer
			if png.Encode(&raster, decoded) != nil {
				return nil, errors.New("GIF参考图无法转换")
			}
			normalized, err := w.scope.mobile.saveAsset(w.scope.id.owner, &raster, false, false)
			if err != nil {
				return nil, err
			}
			w.scope.mobile.mu.Lock()
			item := w.scope.mobile.assets[normalized.id]
			item.holds++
			w.scope.mobile.assets[normalized.id] = item
			w.scope.mobile.mu.Unlock()
			w.held[normalized.id] = true
			w.assets[id] = normalized
		}
		if !slices.Contains(selected, id) {
			selected = append(selected, id)
		}
	}
	w.scope.mu.Lock()
	webCount := len(w.scope.selected)
	w.scope.mu.Unlock()
	if len(selected)+webCount > maximum {
		return nil, denied("图片参考总数量")
	}
	if operation == "edit" && len(selected) == 0 {
		return nil, errors.New("编辑操作至少需要一张参考图片")
	}
	w.selected, w.operation, w.rationale = selected, operation, rationale
	return w.planLocked(), nil
}
func (w *agentWorkspace) planLocked() object {
	return object{"operation": w.operation, "selectedAssetIds": slices.Clone(w.selected), "descriptions": object{}, "rationale": w.rationale, "resultCount": len(w.selected)}
}
func (w *agentWorkspace) plan() object { w.mu.Lock(); defer w.mu.Unlock(); return w.planLocked() }
func (w *agentWorkspace) selectedAssets() []asset {
	w.mu.Lock()
	defer w.mu.Unlock()
	items := []asset{}
	for _, id := range w.selected {
		items = append(items, w.assets[id])
	}
	return items
}
func (w *agentWorkspace) release() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.scope.mobile.mu.Lock()
	defer w.scope.mobile.mu.Unlock()
	for id := range w.held {
		item := w.scope.mobile.assets[id]
		item.holds = max(0, item.holds-1)
		w.scope.mobile.assets[id] = item
	}
	w.held = map[string]bool{}
}

func (g *gateway) agentWorkspace(wr http.ResponseWriter, r *http.Request) {
	lease, selected, ok := g.authorizeAgentLease(wr, r)
	if !ok {
		return
	}
	scope, _ := lease.ctx.Value(agentVisualScopeKey{}).(*agentVisualScope)
	if scope == nil || scope.workspace == nil {
		openAIError(wr, 403, "agent_lease_scope", "当前调用未开放图片工作区")
		return
	}
	var input struct {
		Operation, Kind string
		Offset, Limit   int
		AssetIDs        []string `json:"asset_ids"`
		ImageOperation  string   `json:"image_operation"`
		Rationale       string
	}
	if !decodeRequest(wr, r, 64<<10, &input) {
		return
	}
	var result object
	var err error
	switch input.Operation {
	case "list":
		result, err = scope.workspace.list(selected.key, input.Kind, input.Offset, input.Limit)
	case "view":
		result, err = scope.workspace.view(selected.key, input.AssetIDs)
	case "select":
		result, err = scope.workspace.selectInputs(selected.key, input.ImageOperation, input.AssetIDs, input.Rationale)
	default:
		err = errors.New("工作区操作无效")
	}
	if err != nil {
		openAIError(wr, 403, "workspace_tool_rejected", err.Error())
		return
	}
	if input.Operation == "select" {
		scope.mobile.publish(scope.id, object{"type": "react_continuation_planned", "operation": result["operation"], "selectedAssetIds": result["selectedAssetIds"], "rationale": result["rationale"]}, false)
	}
	writeJSON(wr, 200, result)
}
