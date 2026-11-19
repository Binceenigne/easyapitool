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
	var input struct{ KeyID, Prompt string }
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
	if model == "" && m.g.managedKey(key) {
		configured := m.g.appModels().Reasoning["terra"]
		model, effort = configured.Model, configured.Effort
	}
	if model == "" {
		model = m.g.cfg.reasoningModel
	}
	if model == "" && selected.upstream.URL == m.g.cfg.legacyBase {
		model = "gpt-6.1-terra"
	}
	prompt, _, _, err := m.g.rewrite(r.Context(), selected, model, effort, input.Prompt, false)
	if err != nil {
		writeJSON(w, http.StatusOK, object{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, object{"ok": true, "prompt": prompt})
}

func (m *mobile) preparePrompt(ctx context.Context, id credentialID, key keyInput, ip netip.Addr, input generationInput, settings *modelSettings) (string, object, error) {
	selected, err := m.resolveKey(ctx, key, ip, true)
	if err != nil {
		return "", nil, err
	}
	mode := objectString(input.Options, "reasoningMode")
	preset, effort := "luna", "medium"
	switch mode {
	case "flash":
	case "medium":
		effort = "high"
	case "high", "extra":
		preset, effort = "terra", "xhigh"
	case "max":
		preset, effort = "sol", "xhigh"
	case "advanced":
		preset, effort = objectString(input.Options, "reasoningModel"), objectString(input.Options, "reasoningEffort")
		if !oneOf(preset, "luna", "terra", "sol") || !oneOf(effort, "low", "medium", "high", "xhigh", "max") {
			return "", nil, errors.New("思维模型或深度无效")
		}
	default:
		return "", nil, errors.New("思维模式无效")
	}
	model := ""
	if settings != nil {
		configured := settings.Reasoning[preset]
		model = configured.Model
		if configured.Effort != "" && mode != "advanced" {
			effort = configured.Effort
		}
	} else {
		if m.g.cfg.reasoningEffort != "" && mode != "advanced" {
			effort = m.g.cfg.reasoningEffort
		}
		model = m.g.cfg.reasoningPresets[preset]
		if model == "" {
			model = m.g.cfg.reasoningModel
		}
		if model == "" && selected.upstream.URL == m.g.cfg.legacyBase {
			model = builtinReasoningModels[preset]
		}
		if effort == "max" && selected.upstream.URL == openAIBaseURL {
			effort = "xhigh"
		}
	}
	web := input.Options["webSearchEnabled"] == true
	start := time.Now()
	m.publish(id, object{"type": "react_started", "model": model, "reasoningEffort": effort, "webSearchEnabled": web}, false)
	prompt, usage, searched, err := m.g.rewrite(ctx, selected, model, effort, input.Prompt, web)
	if err != nil {
		m.publish(id, object{"type": "react_failed", "error": err.Error()}, false)
		return "", nil, err
	}
	m.publish(id, object{"type": "react_completed", "prompt": prompt, "model": model, "reasoningEffort": effort,
		"reasoningDurationMs": time.Since(start).Milliseconds(), "reasoningUsage": usage, "webSearchEnabled": web, "webSearchUsed": searched}, false)
	return prompt, usage, nil
}

func (g *gateway) rewrite(ctx context.Context, selected route, model, effort, prompt string, web bool) (string, object, bool, error) {
	if !modelPattern.MatchString(model) {
		return "", nil, false, errors.New("请在网关模型配置中选择可用的思维模型")
	}
	fields := object{"model": model, "input": prompt, "store": false, "max_output_tokens": 8192,
		"instructions": "优化用户的生图提示词，保留原意、语言和引用对象。只输出可直接用于生成或编辑的提示词。"}
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
