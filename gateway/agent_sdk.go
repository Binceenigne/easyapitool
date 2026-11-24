package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptrace"
	"net/netip"
	"net/url"
	"strings"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

type agentEventSinkKey struct{}

// Preserve the provider ledger fields while exposing the client usage protocol.
func sdkClientUsage(raw object) object {
	usage := object{}
	for name, value := range raw {
		usage[name] = value
	}
	available := false
	for source, target := range map[string]string{"input_tokens": "inputTokens", "output_tokens": "outputTokens", "total_tokens": "totalTokens", "requests": "callCount"} {
		if value, valid := tokenCount(raw[source]); valid {
			usage[target] = value
			if source != "requests" {
				available = true
			}
		}
	}
	if details := asObject(raw["input_tokens_details"]); details != nil {
		if value, valid := tokenCount(details["cached_tokens"]); valid {
			usage["cachedTokens"] = value
		}
	}
	usage["hasTokenUsage"] = available
	return usage
}

func loopbackHTTPURL(raw string) bool {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "http" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return false
	}
	ip, err := netip.ParseAddr(parsed.Hostname())
	return err == nil && ip.IsLoopback() && parsed.Port() != ""
}

func (g *gateway) rewriteAgent(ctx context.Context, selected route, model, effort, prompt string, web bool, target object) (string, object, bool, error) {
	maximum := g.cfg.agentAttempts
	if maximum < 1 {
		maximum = 3
	}
	timeout := g.cfg.upstreamTimeout
	if timeout <= 0 {
		timeout = 12 * time.Minute
	}
	timeout = min(timeout, 20*time.Minute)
	callCtx, cancel := context.WithTimeout(ctx, timeout+15*time.Second)
	defer cancel()
	lease, revoke := g.newAgentLease(callCtx, selected, model, effort, objectString(target, "reasoningMode"), web)
	defer revoke()
	requestID := randomID(18)
	request := object{"requestId": requestID, "request": object{
		"base_url": g.cfg.agentProxyBase, "api_key": lease, "model": model, "effort": effort,
		"instructions": imagePromptInstructions(target), "input": prompt, "mode": objectString(target, "reasoningMode"),
		"web_search_enabled": web, "max_turns": 12, "max_output_tokens": 8192, "max_attempts": maximum, "timeout": timeout.Seconds(),
	}}
	planningTurns, _ := visualPlanningBudget(objectString(target, "reasoningMode"), effort)
	if isContinuation(target) {
		planningTurns++
	}
	asObject(request["request"])["max_turns"] = planningTurns
	if scope, ok := ctx.Value(agentVisualScopeKey{}).(*agentVisualScope); ok {
		scope.mode, scope.effort = objectString(target, "reasoningMode"), effort
		input, previewErr := scope.initialInput(prompt)
		if previewErr != nil {
			return "", nil, false, previewErr
		}
		asObject(request["request"])["input"] = input
		if web {
			inspect, adopt, policyErr := scope.limits(selected.key)
			if policyErr != nil {
				return "", nil, false, policyErr
			}
			parameters := asObject(request["request"])
			parameters["visual_reference_limit"], parameters["visual_reference_adopt_limit"] = inspect, adopt
			if inspect > 0 {
				parameters["max_turns"] = min(32, planningTurns+2)
			}
		}
	}
	data, err := json.Marshal(request)
	if err != nil {
		return "", nil, false, operationError("agent", err, 0, maximum)
	}
	client := &http.Client{Transport: &http.Transport{Proxy: nil, DisableKeepAlives: true}, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("worker redirects are disabled") }}
	defer client.CloseIdleConnections()
	stopCancel := context.AfterFunc(callCtx, func() {
		cancelCtx, finish := context.WithTimeout(context.Background(), 2*time.Second)
		defer finish()
		payload, _ := json.Marshal(object{"requestId": requestID})
		cancelReq, _ := http.NewRequestWithContext(cancelCtx, "POST", g.cfg.agentWorkerURL+"/cancel", bytes.NewReader(payload))
		cancelReq.Header.Set("Authorization", "Bearer "+g.cfg.agentWorkerToken)
		cancelReq.Header.Set("Content-Type", "application/json")
		if response, cancelErr := client.Do(cancelReq); cancelErr == nil {
			response.Body.Close()
		}
	})
	defer stopCancel()
	response, err := g.startAgentWorker(callCtx, client, data, maximum)
	if err != nil {
		return "", nil, false, operationError("agent", err, 0, maximum)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		cause := &upstreamCallError{status: response.StatusCode, code: "agent_worker_unavailable", message: "Agent SDK 服务不可用"}
		return "", nil, false, operationError("agent", cause, 0, maximum)
	}
	scanner := bufio.NewScanner(io.LimitReader(response.Body, 16<<20))
	scanner.Buffer(make([]byte, 64<<10), 2<<20)
	for scanner.Scan() {
		var event object
		if json.Unmarshal(scanner.Bytes(), &event) != nil {
			return "", nil, false, operationError("agent", errors.New("Agent SDK 事件无效"), 0, maximum)
		}
		typeName := objectString(event, "type")
		if scope, ok := ctx.Value(agentVisualScopeKey{}).(*agentVisualScope); ok {
			scope.recordEvent(event)
		}
		if typeName == "error" {
			details := asObject(event["error"])
			encoded, _ := json.Marshal(details)
			failure := &operationFailure{Stage: "agent", Code: "agent_failed", MaxAttempts: maximum, DiagnosticID: randomID(9)}
			_ = json.Unmarshal(encoded, failure)
			if failure.LastCause == "" {
				failure.LastCause = objectString(details, "message")
			}
			if failure.LastCause == "" {
				failure.LastCause = objectString(details, "cause")
			}
			failure.LastCause = safeFailureCause(strings.ReplaceAll(failure.LastCause, lease, "[redacted]"))
			return "", nil, false, failure
		}
		if typeName == "result" {
			text := strings.TrimSpace(objectString(event, "text"))
			if text == "" || utf8.RuneCountInString(text) > 32000 {
				return "", nil, false, operationError("agent", errors.New("Agent SDK 未返回完整提示词"), 1, maximum)
			}
			return text, sdkClientUsage(asObject(event["usage"])), event["webSearchUsed"] == true, nil
		}
		if sink, ok := ctx.Value(agentEventSinkKey{}).(func(object)); ok {
			if typeName == "retry" {
				event["type"], event["stage"] = "request_retry", "agent"
				if event["cause"] == nil {
					event["cause"] = event["error"]
				}
			} else if typeName == "text_delta" {
				event["type"] = "react_turn_delta"
				sink(event)
				copy := object{}
				for name, value := range event {
					copy[name] = value
				}
				copy["type"] = "react_summary_delta"
				sink(copy)
				continue
			} else if typeName == "turn_started" {
				event["type"], event["title"] = "react_turn_started", "正在完善创作方案"
			} else if typeName == "turn_completed" {
				event["type"], event["title"] = "react_turn_completed", "本轮方案已更新"
			} else if typeName == "tool_started" {
				event["type"] = "react_tool_started"
			} else if typeName == "tool_completed" {
				event["type"] = "react_tool_completed"
			} else if typeName == "tool_failed" {
				event["type"] = "react_tool_failed"
			} else if strings.HasPrefix(typeName, "web_search_") {
				event["tool"] = "web_search"
				event["type"] = map[string]string{"web_search_started": "react_tool_started", "web_search_completed": "react_tool_completed", "web_search_failed": "react_tool_failed"}[typeName]
			}
			if event["type"] != "" && typeName != "heartbeat" {
				sink(event)
			}
		}
	}
	if err := callCtx.Err(); err != nil {
		return "", nil, false, operationError("agent", err, 0, maximum)
	}
	err = scanner.Err()
	if err == nil {
		err = io.ErrUnexpectedEOF
	}
	return "", nil, false, operationError("agent", err, 0, maximum)
}

func (g *gateway) startAgentWorker(ctx context.Context, client *http.Client, data []byte, maximum int) (*http.Response, error) {
	for attempt := 1; attempt <= maximum; attempt++ {
		var written atomic.Bool
		trace := &httptrace.ClientTrace{WroteRequest: func(info httptrace.WroteRequestInfo) {
			if info.Err == nil {
				written.Store(true)
			}
		}}
		req, err := http.NewRequestWithContext(httptrace.WithClientTrace(ctx, trace), "POST", g.cfg.agentWorkerURL+"/run", bytes.NewReader(data))
		if err != nil {
			return nil, operationError("agent", err, attempt, maximum)
		}
		req.Header.Set("Authorization", "Bearer "+g.cfg.agentWorkerToken)
		req.Header.Set("Content-Type", "application/json")
		response, callErr := client.Do(req)
		if callErr == nil && response.StatusCode != 429 && response.StatusCode != 503 {
			return response, nil
		}
		var failure *operationFailure
		if callErr != nil {
			failure = operationError("agent", callErr, attempt, maximum)
			// A completely written POST may already have started SDK/model work.
			// Only demonstrably pre-run connection failures may be replayed here.
			if written.Load() {
				return nil, failure
			}
		} else {
			io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
			response.Body.Close()
			failure = operationError("agent", &upstreamCallError{status: response.StatusCode, code: "agent_worker_busy", message: "Agent SDK 服务繁忙，尚未开始模型调用", retryAfter: parseRetryAfter(response.Header.Get("Retry-After"), time.Now())}, attempt, maximum)
		}
		if ctx.Err() != nil {
			return nil, operationError("agent", ctx.Err(), attempt, maximum)
		}
		delay := retryDelay(failure, attempt)
		if !failure.Retryable || attempt == maximum || delay > 2*time.Minute {
			return nil, failure
		}
		if sink, ok := ctx.Value(agentEventSinkKey{}).(func(object)); ok {
			sink(object{"type": "request_retry", "stage": "agent", "attempt": attempt + 1, "maxAttempts": maximum, "delayMs": delay.Milliseconds(), "cause": failure.LastCause, "status": failure.Status, "diagnosticId": failure.DiagnosticID})
		}
		if err := waitForRetry(ctx, delay); err != nil {
			return nil, operationError("agent", err, attempt, maximum)
		}
	}
	return nil, operationError("agent", errors.New("Agent SDK 接入失败"), maximum, maximum)
}
