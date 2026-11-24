package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"mime/multipart"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const maxProxyBody = 825 << 20

type gateway struct {
	cfg            config
	store          *store
	client         *http.Client
	egress         *egressManager
	onQuotaChange  func(string, int64)
	modelMu        sync.Mutex
	modelProfile   atomic.Pointer[modelSettings]
	onModelsChange func(int64)
	agentMu        sync.Mutex
	agentLeases    map[string]*agentProxyLease
}

func newUpstreamClient(cfg config, transport http.RoundTripper) *http.Client {
	return &http.Client{
		Transport: transport,
		Timeout:   cfg.upstreamTimeout,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return errors.New("upstream redirects are disabled")
		},
	}
}

func (g *gateway) upstreamRequest(ctx context.Context, u upstream, method, path, query string, body io.Reader) (*http.Request, error) {
	base, err := g.cfg.baseURL(u.URL)
	if err != nil {
		return nil, err
	}
	base.Path += path
	base.RawQuery = query
	req, err := http.NewRequestWithContext(ctx, method, base.String(), body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+u.Secret)
	return req, nil
}

func (g *gateway) registerProxy(mux *http.ServeMux) {
	mux.HandleFunc("POST /internal/agent/v1/responses", g.agentProxy)
	mux.HandleFunc("POST /internal/agent/v1/reference-images", g.agentVisual)
	for _, path := range []string{"/images/generations", "/images/edits", "/images/variations", "/responses"} {
		mux.HandleFunc("POST /v1"+path, g.proxy(path))
	}
	mux.HandleFunc("GET /v1/models", g.proxy("/models"))
	mux.HandleFunc("GET /v1/usage", g.proxyUsage)
	mux.HandleFunc("GET /v1/usage/events", g.quotaEvents)
}

func bearer(r *http.Request) string {
	scheme, token, _ := strings.Cut(r.Header.Get("Authorization"), " ")
	if !strings.EqualFold(scheme, "Bearer") || len(token) > 4096 {
		return ""
	}
	return strings.TrimSpace(token)
}

func jsonError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, object{"detail": message})
}

func (g *gateway) proxyError(w http.ResponseWriter, err error) {
	status, code, message := http.StatusServiceUnavailable, "gateway_unavailable", "网关暂时不可用"
	var limited *quotaError
	var rpm *rpmError
	switch {
	case errors.As(err, &rpm):
		w.Header().Set("Retry-After", strconv.Itoa(rpm.retryAfter()))
		writeJSON(w, http.StatusTooManyRequests, object{"error": object{"type": "rate_limit_exceeded", "code": "rpm_limit_exceeded",
			"message": rpm.Error(), "rateLimit": rpm.state.view()}, "rateLimit": rpm.state.view()})
		return
	case errors.Is(err, errInvalidKey):
		status, code, message = http.StatusUnauthorized, "invalid_api_key", errInvalidKey.Error()
	case errors.Is(err, errKeyIP):
		status, code, message = http.StatusForbidden, "ip_not_allowed", err.Error()
	case errors.Is(err, errPricingUnavailable):
		status, code, message = http.StatusServiceUnavailable, "pricing_unavailable", err.Error()
	case errors.As(err, &limited):
		status, code, message = http.StatusTooManyRequests, "rate_limit_exceeded", limited.Error()
		detail := object{"window": limited.window, "unit": "USD", "limit": float64(limited.limit) / 1e6, "used": float64(limited.used) / 1e6,
			"remaining": float64(max(0, limited.limit-limited.used)) / 1e6, "requiredReserve": float64(limited.reserve) / 1e6, "reset_at": nil}
		if !limited.resetAt.IsZero() {
			detail["reset_at"] = limited.resetAt.UTC()
			w.Header().Set("Retry-After", strconv.Itoa(int(max(1, time.Until(limited.resetAt).Seconds()+1))))
		}
		writeJSON(w, status, object{"error": object{"type": code, "code": code, "message": message, "quota": detail}})
		return
	default:
		var tier *tierError
		if errors.As(err, &tier) {
			openAIError(w, http.StatusForbidden, "tier_permission_denied", tier.Error())
			return
		}
		log.Printf("gateway storage error: %v", err)
	}
	openAIError(w, status, code, message)
}

func openAIError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, object{"error": object{"type": code, "code": code, "message": message}})
}

func (g *gateway) proxyUsage(w http.ResponseWriter, r *http.Request) {
	selected, err := g.resolveRoute(r.Context(), bearer(r), g.clientIP(r), false)
	if err != nil {
		g.proxyError(w, err)
		return
	}
	payload, err := g.store.usage(r.Context(), selected.key, time.Now())
	if err != nil {
		g.proxyError(w, err)
		return
	}
	payload["appModels"], err = g.modelsForKey(selected.key)
	if err != nil {
		g.proxyError(w, err)
		return
	}
	if profile, ok := payload["appModels"].(*modelSettings); ok {
		payload["planName"] = profile.Tier.Name
	}
	writeJSON(w, http.StatusOK, payload)
}

func (g *gateway) proxy(path string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		selected, err := g.proxyRoute(r)
		if err != nil {
			g.proxyError(w, err)
			return
		}
		u := selected.upstream
		var body io.Reader
		metadata := func() requestMetadata { return requestMetadata{} }
		if r.Method == http.MethodPost {
			if r.ContentLength > maxProxyBody {
				openAIError(w, http.StatusRequestEntityTooLarge, "body_too_large", "请求体超过网关上限")
				return
			}
			if r.ContentLength == 0 {
				openAIError(w, http.StatusBadRequest, "empty_body", "请求体为空")
				return
			}
			body = http.MaxBytesReader(w, r.Body, maxProxyBody)
			var cleanup func()
			body, cleanup, err = g.authorizeProxyBody(r, selected.key, path, body)
			if err != nil {
				var denied *tierError
				var tooLarge *http.MaxBytesError
				if errors.As(err, &denied) {
					g.proxyError(w, err)
				} else if errors.As(err, &tooLarge) {
					openAIError(w, 413, "body_too_large", "请求体超过网关上限")
				} else {
					openAIError(w, 400, "invalid_request", err.Error())
				}
				return
			}
			defer cleanup()
			if len(g.cfg.pricing) > 0 && r.Header.Get("Content-Encoding") == "" {
				body, metadata = inspectRequest(body, r.Header.Get("Content-Type"))
			}
		}
		req, err := g.upstreamRequest(r.Context(), u, r.Method, path, r.URL.RawQuery, body)
		if err != nil {
			metadata()
			openAIError(w, http.StatusBadGateway, "upstream_configuration", "上游地址配置无效")
			return
		}
		req.ContentLength = r.ContentLength
		for _, name := range []string{"Content-Type", "Content-Encoding", "Accept", "OpenAI-Beta", "Idempotency-Key", "X-Client-Request-Id"} {
			if value := r.Header.Get(name); value != "" {
				req.Header.Set(name, value)
			}
		}
		var reservation int64
		if r.Method == http.MethodPost {
			reservation, err = g.reserve(r.Context(), selected)
			if err != nil {
				metadata()
				g.proxyError(w, err)
				return
			}
		}
		status := 0
		var count *meter
		observer := usageObserver{}
		defer func() {
			request := metadata()
			model := observer.model
			if model == "" {
				model = request.model
			}
			if request.withExtras || !standardTier(observer.serviceTier) {
				model = ""
			}
			g.recordPriced(reservation, status, count, model, observer.outputFormat == "svg" || request.format == "svg" || observer.svgUsage != nil)
		}()
		response, err := g.client.Do(req)
		if err != nil {
			status = http.StatusBadGateway
			count = unsentImageUsage(err)
			var limit *http.MaxBytesError
			if errors.As(err, &limit) {
				status = http.StatusRequestEntityTooLarge
				openAIError(w, status, "body_too_large", "请求体超过网关上限")
				return
			}
			message := g.connectionError(u, err)
			openAIError(w, status, "upstream_unreachable", message.Error())
			return
		}
		defer response.Body.Close()
		status = response.StatusCode
		for name, values := range response.Header {
			if oneOf(name, "Content-Type", "Content-Encoding", "Content-Length", "Content-Disposition", "X-Request-Id", "Request-Id", "Retry-After", "Openai-Processing-Ms") || strings.HasPrefix(strings.ToLower(name), "x-ratelimit-") {
				for _, value := range values {
					w.Header().Add(name, value)
				}
			}
		}
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		kind := response.Header.Get("Content-Type")
		if _, internal := r.Context().Value(agentRouteContextKey{}).(route); internal && status >= 400 {
			problem := upstreamStatus(response).(*upstreamCallError)
			message := problem.message
			if u.Secret != "" {
				message = strings.ReplaceAll(message, u.Secret, "[redacted]")
			}
			w.Header().Del("Content-Length")
			w.Header().Set("X-Mirra-Diagnostic-Id", problem.diagnostic)
			openAIError(w, status, problem.code, message)
			return
		}
		stream := strings.HasPrefix(kind, "text/event-stream")
		if stream {
			w.Header().Set("X-Accel-Buffering", "no")
		}
		w.WriteHeader(status)
		observer.sse = stream
		var reader io.Reader = response.Body
		visualObserver := visualObserverForRequest(r, stream)
		if visualObserver != nil && status >= 200 && status < 300 && response.Header.Get("Content-Encoding") == "" {
			reader = io.TeeReader(reader, visualObserver)
		}
		if reservation != 0 && status >= 200 && status < 300 && (stream || strings.HasPrefix(kind, "application/json")) && response.Header.Get("Content-Encoding") == "" {
			// Observe only metadata; the upstream body is forwarded immediately.
			reader = io.TeeReader(reader, &observer)
		}
		delivered, readErr := transfer(w, reader, stream)
		if visualObserver != nil {
			visualObserver.finish()
		}
		count = observer.meter()
		if readErr != nil && r.Context().Err() == nil {
			log.Printf("upstream response interrupted upstream=%s path=%s bytes=%d reason=%s", u.ID, path, delivered, upstreamFailureReason(readErr))
			if status >= 200 && status < 300 {
				status = http.StatusBadGateway
			}
			// Headers were already sent. Abort the downstream response so clients
			// can distinguish a truncated body from a complete HTTP 200 response.
			panic(http.ErrAbortHandler)
		}
	}
}

func transfer(w http.ResponseWriter, reader io.Reader, stream bool) (int64, error) {
	controller := http.NewResponseController(w)
	if stream && controller.Flush() != nil {
		return 0, nil
	}
	var delivered int64
	buf := make([]byte, 32<<10)
	for {
		n, err := reader.Read(buf)
		if n != 0 {
			written, writeErr := w.Write(buf[:n])
			delivered += int64(written)
			if writeErr != nil || (stream && controller.Flush() != nil) {
				return delivered, nil
			}
		}
		if err == io.EOF {
			return delivered, nil
		}
		if err != nil {
			return delivered, err
		}
	}
}

func (g *gateway) connectionError(u upstream, err error) error {
	trace := randomID(9)
	log.Printf("upstream connection error trace=%s upstream=%s reason=%s", trace, u.ID, upstreamFailureReason(err))
	var rejection *proxyConnectError
	if errors.As(err, &rejection) {
		return &upstreamCallError{status: rejection.status, code: "proxy_connect_" + strconv.Itoa(rejection.status), message: fmt.Sprintf("%s（诊断 ID %s）", rejection.Error(), trace), diagnostic: trace, cause: err}
	}
	reason := upstreamFailureReason(err)
	return &upstreamCallError{status: 502, code: reason, message: fmt.Sprintf("无法连接上游服务：%s（诊断 ID %s）", reason, trace), diagnostic: trace, cause: err}
}

func upstreamFailureReason(err error) string {
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		return "timeout"
	case errors.Is(err, context.Canceled):
		return "canceled"
	case errors.Is(err, io.ErrUnexpectedEOF), errors.Is(err, io.EOF):
		return "unexpected_eof"
	case errors.Is(err, syscall.ECONNREFUSED):
		return "connection_refused"
	case errors.Is(err, syscall.ECONNRESET):
		return "connection_reset"
	case errors.Is(err, syscall.EHOSTUNREACH):
		return "host_unreachable"
	case errors.Is(err, syscall.ENETUNREACH):
		return "network_unreachable"
	}
	var connect *proxyConnectError
	if errors.As(err, &connect) {
		return "proxy_connect_" + strconv.Itoa(connect.status)
	}
	var network net.Error
	if errors.As(err, &network) && network.Timeout() {
		return "timeout"
	}
	for cause := err; cause != nil; cause = errors.Unwrap(cause) {
		switch cause.Error() {
		case "http2: client connection lost", "http2: client connection force closed via ClientConn.Close":
			return "http2_connection_lost"
		case "net/http: TLS handshake timeout":
			return "tls_handshake_timeout"
		}
	}
	return fmt.Sprintf("%T", err)
}

func (g *gateway) recordPriced(id int64, status int, count *meter, model string, svg bool) {
	if id == 0 {
		return
	}
	cost := g.cfg.price(model, svg, count)
	if count != nil && count.notSent {
		zero := int64(0)
		cost = &zero
	}
	switch status {
	case 400, 401, 403, 404, 413, 415, 422, 429:
		if count == nil {
			zero := int64(0)
			cost = &zero
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := g.store.finish(ctx, id, status, count, cost); err != nil {
		log.Printf("could not record usage for request %d: %v", id, err)
	}
}

type requestMetadata struct {
	model, format string
	withExtras    bool
}

func standardTier(tier string) bool { return oneOf(tier, "", "auto", "default", "standard") }

// Capture multipart model fields without buffering the uploaded image parts.
func inspectRequest(body io.Reader, contentType string) (io.Reader, func() requestMetadata) {
	kind, parameters, _ := mime.ParseMediaType(contentType)
	if kind != "application/json" && (kind != "multipart/form-data" || parameters["boundary"] == "") {
		return body, func() requestMetadata { return requestMetadata{} }
	}
	reader, writer := io.Pipe()
	finished := make(chan requestMetadata, 1)
	go func() {
		defer reader.Close()
		var metadata requestMetadata
		if kind == "application/json" {
			var observer usageObserver
			io.Copy(&observer, reader)
			metadata = requestMetadata{model: observer.model, format: observer.outputFormat,
				withExtras: observer.withTools || !standardTier(observer.serviceTier)}
		} else {
			parts := multipart.NewReader(reader, parameters["boundary"])
			for {
				part, err := parts.NextRawPart()
				if err != nil {
					break
				}
				name := part.FormName()
				if part.FileName() == "" && oneOf(name, "model", "output_format") {
					value, err := io.ReadAll(io.LimitReader(part, 129))
					if err == nil && len(value) <= 128 {
						if name == "model" {
							metadata.model = string(value)
						} else {
							metadata.format = string(value)
						}
					}
				}
				part.Close()
			}
			io.Copy(io.Discard, reader)
		}
		finished <- metadata
	}()
	return io.TeeReader(body, writer), func() requestMetadata {
		writer.Close()
		return <-finished
	}
}

func decodeRequest(w http.ResponseWriter, r *http.Request, limit int64, value any) bool {
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, limit))
	if err := decoder.Decode(value); err != nil {
		jsonError(w, http.StatusBadRequest, "JSON 请求格式无效")
		return false
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		jsonError(w, http.StatusBadRequest, "JSON 请求格式无效")
		return false
	}
	return true
}

func encodeJSON(w io.Writer, value any) error { return json.NewEncoder(w).Encode(value) }

func upstreamStatus(response *http.Response) error {
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		return nil
	}
	id := response.Header.Get("X-Request-Id")
	if id == "" || len(id) > 128 {
		id = randomID(9)
	}
	data, _ := io.ReadAll(io.LimitReader(response.Body, 64<<10))
	var payload object
	_ = json.Unmarshal(data, &payload)
	detail := asObject(payload["error"])
	code := objectString(detail, "code")
	if code == "" {
		code = fmt.Sprintf("upstream_http_%d", response.StatusCode)
	}
	message := objectString(detail, "message")
	if message == "" {
		message = objectString(payload, "detail")
	}
	if message == "" {
		message = http.StatusText(response.StatusCode)
	}
	return &upstreamCallError{status: response.StatusCode, code: code, diagnostic: id,
		message:    fmt.Sprintf("上游返回 HTTP %d：%s（请求 ID %s）", response.StatusCode, safeFailureCause(message), id),
		retryAfter: parseRetryAfter(response.Header.Get("Retry-After"), time.Now())}
}
