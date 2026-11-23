package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type upstreamCallError struct {
	status                    int
	code, message, diagnostic string
	retryAfter                time.Duration
	cause                     error
}

func (e *upstreamCallError) Error() string { return e.message }
func (e *upstreamCallError) Unwrap() error { return e.cause }

type operationFailure struct {
	Stage        string `json:"stage"`
	Code         string `json:"code"`
	Attempts     int    `json:"attempts"`
	MaxAttempts  int    `json:"maxAttempts"`
	LastCause    string `json:"lastCause"`
	Status       int    `json:"status"`
	DiagnosticID string `json:"diagnosticId"`
	Retryable    bool   `json:"retryable"`
	cause        error
	delay        time.Duration
}

func (e *operationFailure) Error() string {
	label := map[string]string{"agent": "智能处理", "image": "图片生成"}[e.Stage]
	if label == "" {
		label = e.Stage
	}
	return fmt.Sprintf("%s失败（%d/%d 次，%s）：%s（诊断 ID %s）", label, e.Attempts, e.MaxAttempts, e.Code, e.LastCause, e.DiagnosticID)
}
func (e *operationFailure) Unwrap() error { return e.cause }

func (e *operationFailure) view() object {
	return object{"stage": e.Stage, "code": e.Code, "attempts": e.Attempts, "maxAttempts": e.MaxAttempts, "cause": e.LastCause,
		"lastCause": e.LastCause, "status": e.Status, "diagnosticId": e.DiagnosticID, "retryable": e.Retryable}
}

var secretInFailure = regexp.MustCompile(`(?i)(?:sk-[a-z0-9_-]{8,}|lease-[a-z0-9_-]{12,}|bearer\s+[^\s,;]+)`)

func safeFailureCause(message string) string {
	message = secretInFailure.ReplaceAllString(message, "[redacted]")
	message = strings.Map(func(r rune) rune {
		if r < 32 && r != '\n' && r != '\t' {
			return -1
		}
		return r
	}, message)
	runes := []rune(message)
	if len(runes) > 1500 {
		message = string(runes[:1500]) + "…"
	}
	return message
}

func operationError(stage string, err error, attempt, maximum int) *operationFailure {
	var existing *operationFailure
	if errors.As(err, &existing) {
		return existing
	}
	f := &operationFailure{Stage: stage, Code: "request_failed", Attempts: attempt, MaxAttempts: maximum,
		LastCause: safeFailureCause(err.Error()), DiagnosticID: randomID(9), cause: err}
	var upstream *upstreamCallError
	var tier *tierError
	var quota *quotaError
	var rpm *rpmError
	var network net.Error
	switch {
	case errors.Is(err, context.Canceled):
		f.Code, f.Status = "cancelled", 499
	case errors.As(err, &rpm):
		f.Code, f.Status = "rpm_limit_exceeded", 429
	case errors.As(err, &quota):
		f.Code, f.Status = "quota_exceeded", 429
	case errors.Is(err, errInvalidKey):
		f.Code, f.Status = "invalid_api_key", 401
	case errors.Is(err, errKeyIP):
		f.Code, f.Status = "ip_not_allowed", 403
	case errors.Is(err, errPricingUnavailable):
		f.Code, f.Status = "pricing_unavailable", 503
	case errors.As(err, &tier):
		f.Code, f.Status = "tier_permission_denied", 403
	case errors.As(err, &upstream):
		f.Code, f.Status, f.delay = upstream.code, upstream.status, upstream.retryAfter
		if upstream.diagnostic != "" {
			f.DiagnosticID = upstream.diagnostic
		}
		f.Retryable = (upstream.status >= 500 || upstream.status == 429 || upstream.status == 408) && !oneOf(upstream.code, "insufficient_quota", "quota_exceeded", "rpm_limit_exceeded", "pricing_unavailable", "invalid_request_error", "unsupported_parameter", "content_policy_violation", "content_filter", "tls_certificate_invalid")
	case errors.Is(err, context.DeadlineExceeded):
		f.Code, f.Status, f.Retryable = "upstream_timeout", 504, true
	case errors.As(err, &network), errors.Is(err, io.ErrUnexpectedEOF), errors.Is(err, io.EOF):
		f.Code, f.Status, f.Retryable = "upstream_connection", 502, true
	}
	return f
}

func attachOperationFailure(target object, err error) {
	var failure *operationFailure
	if errors.As(err, &failure) {
		target["errorDetails"] = failure.view()
	}
}

func parseRetryAfter(value string, now time.Time) time.Duration {
	if seconds, err := strconv.ParseFloat(strings.TrimSpace(value), 64); err == nil && seconds > 0 {
		if math.IsInf(seconds, 0) || seconds > 365*24*3600 {
			return 365 * 24 * time.Hour
		}
		return time.Duration(seconds * float64(time.Second))
	}
	if date, err := http.ParseTime(value); err == nil && date.After(now) {
		return date.Sub(now)
	}
	return 0
}

func retryDelay(f *operationFailure, attempt int) time.Duration {
	if f.delay > 0 {
		return f.delay
	}
	return time.Duration(1<<min(attempt-1, 4)) * time.Second
}

func attemptLimit(value int) int {
	if value < 1 {
		return 3
	}
	return value
}

func waitForRetry(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
