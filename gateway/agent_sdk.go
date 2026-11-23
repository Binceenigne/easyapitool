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
			return text, asObject(event["usage"]), event["webSearchUsed"] == true, nil
		}
		if sink, ok := ctx.Value(agentEventSinkKey{}).(func(object)); ok {
			if typeName == "retry" {
				event["type"], event["stage"] = "request_retry", "agent"
				if event["cause"] == nil {
					event["cause"] = event["error"]
				}
			} else if typeName == "text_delta" {
				event["type"] = "react_delta"
			}
			sink(event)
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
