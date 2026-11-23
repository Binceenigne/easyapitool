package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/netip"
	"strings"
	"time"
	"unicode/utf8"
)

func (m *mobile) polish(w http.ResponseWriter, r *http.Request, owner string) {
	var input struct {
		KeyID, Prompt string
		Options       object
	}
	if !decodeRequest(w, r, 160<<10, &input) {
		return
	}
	input.Prompt = strings.TrimSpace(input.Prompt)
	if input.Prompt == "" || utf8.RuneCountInString(input.Prompt) > 32000 {
		jsonError(w, http.StatusBadRequest, "提示词为空或过长")
		return
	}
	key, err := m.getKey(owner, input.KeyID)
	if err != nil {
		jsonError(w, http.StatusNotFound, err.Error())
		return
	}
	selected, err := m.resolveKey(r.Context(), key, m.g.clientIP(r), true)
	if err != nil {
		writeJSON(w, http.StatusOK, object{"ok": false, "error": err.Error()})
		return
	}
	model := m.g.cfg.polishModel
	effort := m.g.cfg.reasoningEffort
	if selected.key.ID != "" {
		profile, err := m.g.modelsForKey(selected.key)
		if err != nil {
			m.g.proxyError(w, err)
			return
		}
		model, effort = profile.Polish.Model, profile.Polish.Effort
	}
	if model == "" && selected.key.ID == "" {
		model = m.g.cfg.reasoningModel
	}
	if model == "" && selected.upstream.URL == m.g.cfg.legacyBase {
		model = "gpt-6.1-terra"
	}
	if input.Options == nil {
		input.Options = object{}
	}
	input.Options["reasoningMode"] = "polish"
	prompt, _, _, err := m.g.rewrite(r.Context(), selected, model, effort, input.Prompt, false, input.Options)
	if err != nil {
		if rpmFailure(err) != nil {
			m.g.proxyError(w, err)
			return
		}
		failure := operationError("agent", err, 0, attemptLimit(m.g.cfg.agentAttempts))
		payload := object{"ok": false, "error": failure.Error()}
		attachOperationFailure(payload, failure)
		writeJSON(w, http.StatusOK, payload)
		return
	}
	writeJSON(w, http.StatusOK, object{"ok": true, "prompt": prompt})
}

func (m *mobile) preparePrompt(ctx context.Context, id credentialID, key keyInput, ip netip.Addr, input generationInput, settings *modelSettings) (string, object, error) {
	mode := objectString(input.Options, "reasoningMode")
	hiddenContinuation := (mode == "" || mode == "instant") && isContinuation(input.Options)
	slot, ok := normalReasoningSlots[mode]
	model, effort := "", ""
	if mode == "advanced" {
		slot, effort = objectString(input.Options, "reasoningModel"), objectString(input.Options, "reasoningEffort")
		if !oneOf(slot, "luna", "terra", "sol") || !oneOf(effort, "", "low", "medium", "high", "xhigh", "max") {
			return "", nil, errors.New("思维模型或深度无效")
		}
	} else if !ok && !hiddenContinuation {
		return "", nil, errors.New("思维模式无效")
	}
	selected, err := m.resolveKey(ctx, key, ip, true)
	if err != nil {
		return "", nil, err
	}
	if settings != nil {
		if hiddenContinuation {
			model, effort = settings.Continuation.Model, settings.Continuation.Effort
		} else if mode == "advanced" {
			model = settings.Reasoning[slot].Model
		} else {
			preset := settings.ReasoningModes[mode]
			model, effort = preset.Model, preset.Effort
		}
	} else {
		if mode != "advanced" {
			effort = m.g.cfg.reasoningEffort
			if effort == "" {
				effort = builtinReasoningDepth[slot]
				if mode == "medium" {
					effort = "high"
				}
			}
		}
		model = m.g.cfg.reasoningPresets[slot]
		if model == "" {
			model = m.g.cfg.reasoningModel
		}
		if model == "" && selected.upstream.URL == m.g.cfg.legacyBase {
			model = builtinReasoningModels[slot]
		}
	}
	if hiddenContinuation {
		if settings == nil {
			model, effort = m.g.cfg.reasoningPresets["luna"], "low"
			if model == "" {
				model = m.g.cfg.reasoningModel
			}
			if model == "" {
				model = builtinReasoningModels["luna"]
			}
		}
		copy := object{}
		for name, value := range input.Options {
			copy[name] = value
		}
		copy["reasoningMode"] = "continuation"
		input.Options = copy
	}
	effort = supportedReasoningEffort(model, effort)
	web := input.Options["webSearchEnabled"] == true
	start := time.Now()
	m.publish(id, object{"type": "react_started", "model": model, "reasoningEffort": effort, "webSearchEnabled": web}, false)
	ctx = context.WithValue(ctx, agentEventSinkKey{}, func(event object) { m.publish(id, event, false) })
	prompt, usage, searched, err := m.g.rewrite(ctx, selected, model, effort, input.Prompt, web, input.Options)
	if err != nil {
		failed := object{"type": "react_failed", "error": err.Error()}
		attachOperationFailure(failed, err)
		m.publish(id, failed, false)
		return "", nil, err
	}
	m.publish(id, object{"type": "react_completed", "prompt": prompt, "model": model, "reasoningEffort": effort,
		"reasoningDurationMs": time.Since(start).Milliseconds(), "reasoningUsage": usage, "webSearchEnabled": web, "webSearchUsed": searched}, false)
	return prompt, usage, nil
}

func isContinuation(options object) bool {
	return options["continuation"] == true || objectString(options, "parentSetId") != "" || objectString(options, "previousPrompt") != ""
}

func (g *gateway) rewrite(ctx context.Context, selected route, model, effort, prompt string, web bool, options ...object) (string, object, bool, error) {
	if !modelPattern.MatchString(model) {
		return "", nil, false, errors.New("请在网关模型配置中选择可用的思维模型")
	}
	effort = supportedReasoningEffort(model, effort)
	var target object
	if len(options) > 0 {
		target = options[0]
	}
	if err := g.checkReasoningTier(selected.key, model, effort, objectString(target, "reasoningMode"), web); err != nil {
		return "", nil, false, err
	}
	if g.cfg.agentWorkerURL != "" {
		return g.rewriteAgent(ctx, selected, model, effort, prompt, web, target)
	}
	fields := object{"model": model, "input": prompt, "store": false, "max_output_tokens": 8192,
		"instructions": imagePromptInstructions(target)}
	if effort != "" {
		fields["reasoning"] = object{"effort": effort}
	}
	if web {
		fields["tools"] = []object{{"type": "web_search"}}
		fields["max_tool_calls"] = 3
	}
	data, err := json.Marshal(fields)
	if err != nil {
		return "", nil, false, err
	}
	req, err := g.upstreamRequest(ctx, selected.upstream, http.MethodPost, "/responses", "", bytes.NewReader(data))
	if err != nil {
		return "", nil, false, err
	}
	req.Header.Set("Content-Type", "application/json")
	reservation, err := g.reserve(ctx, selected)
	if err != nil {
		return "", nil, false, err
	}
	status := 0
	var count *meter
	defer func() {
		if web {
			model = ""
		}
		g.recordPriced(reservation, status, count, model, false)
	}()
	resp, err := g.client.Do(req)
	if err != nil {
		status = http.StatusBadGateway
		return "", nil, false, g.connectionError(selected.upstream, err)
	}
	defer resp.Body.Close()
	status = resp.StatusCode
	if err := upstreamStatus(resp); err != nil {
		return "", nil, false, err
	}
	var output struct {
		Model, Status, OutputText string
		Usage                     object
		Output                    []struct {
			Type    string
			Content []struct{ Type, Text string }
		}
	}
	var payload object
	data, err = io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	if err != nil || json.Unmarshal(data, &output) != nil || json.Unmarshal(data, &payload) != nil {
		return "", nil, false, errors.New("思维模型响应无效或过大")
	}
	if output.Model != "" {
		if _, exists := g.cfg.pricing[output.Model]; exists {
			model = output.Model
		}
	}
	count = usageMeter(output.Usage, nil)
	text, searched := objectString(payload, "output_text"), false
	for _, item := range output.Output {
		searched = searched || item.Type == "web_search_call"
		for _, content := range item.Content {
			if content.Type == "output_text" {
				output.OutputText += content.Text
			}
		}
	}
	if text == "" {
		text = output.OutputText
	}
	text = strings.TrimSpace(text)
	if text == "" || output.Status == "failed" || output.Status == "incomplete" || utf8.RuneCountInString(text) > 32000 {
		return "", output.Usage, searched, errors.New("思维模型没有返回完整的提示词")
	}
	return text, output.Usage, searched, nil
}
