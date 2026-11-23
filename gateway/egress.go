package main

import (
	"context"
	"crypto/tls"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode"
)

type proxySettings struct {
	Mode     string `json:"mode"`
	Scope    string `json:"scope"`
	URL      string `json:"url,omitempty"`
	Revision int64  `json:"revision"`
}

type egressManager struct {
	mu        sync.RWMutex
	store     *store
	envURL    string
	settings  proxySettings
	transport *egressTransport
}

type egressTransport struct {
	shared *http.Transport
	images *http.Transport
}

func (t *egressTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if t.images != nil && req.Method == http.MethodPost &&
		strings.EqualFold(req.URL.Hostname(), "api.openai.com") &&
		strings.HasPrefix(req.URL.Path, "/v1/images/") {
		return t.imageRoundTrip(req)
	}
	return t.shared.RoundTrip(req)
}

// Transport closes request bodies even when CONNECT or TLS fails. Keep the
// caller's streaming body intact until a connection has actually been obtained.
type pendingImageBody struct {
	body io.ReadCloser
	read *atomic.Bool
}

func (b pendingImageBody) Read(p []byte) (int, error) {
	b.read.Store(true)
	return b.body.Read(p)
}

func (pendingImageBody) Close() error { return nil }

type upstreamNotSentError struct{ error }

type imageRetryOwnerContextKey struct{}

func (e *upstreamNotSentError) Unwrap() error { return e.error }

func unsentImageUsage(err error) *meter {
	var unsent *upstreamNotSentError
	if errors.As(err, &unsent) {
		return &meter{notSent: true}
	}
	return nil
}

func (t *egressTransport) imageRoundTrip(req *http.Request) (*http.Response, error) {
	if req.Body != nil {
		defer req.Body.Close()
	}
	for attempt := 0; ; attempt++ {
		var connected, bodyRead atomic.Bool
		trace := &httptrace.ClientTrace{GotConn: func(httptrace.GotConnInfo) { connected.Store(true) }}
		current := req.Clone(httptrace.WithClientTrace(req.Context(), trace))
		if req.Body != nil && req.Body != http.NoBody {
			current.Body = pendingImageBody{req.Body, &bodyRead}
		}
		response, err := t.images.RoundTrip(current)
		if err == nil || connected.Load() || bodyRead.Load() {
			// Once TLS succeeds, a POST may have reached OpenAI even without a
			// response. Replaying it could start another paid generation.
			return response, err
		}
		unsent := &upstreamNotSentError{err}
		owned, _ := req.Context().Value(imageRetryOwnerContextKey{}).(bool)
		if owned || attempt != 0 || req.Context().Err() != nil || !retryImageConnection(err) {
			return nil, unsent
		}
		log.Printf("retrying unsent image connection path=%s reason=%s", req.URL.Path, upstreamFailureReason(err))
		timer := time.NewTimer(350 * time.Millisecond)
		select {
		case <-req.Context().Done():
			timer.Stop()
			return nil, &upstreamNotSentError{req.Context().Err()}
		case <-timer.C:
		}
	}
}

func retryImageConnection(err error) bool {
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}
	var rejection *proxyConnectError
	if errors.As(err, &rejection) {
		return rejection.status >= 500
	}
	var certificate *tls.CertificateVerificationError
	if errors.As(err, &certificate) {
		return false
	}
	return true
}

func (t *egressTransport) CloseIdleConnections() {
	t.shared.CloseIdleConnections()
	if t.images != nil {
		t.images.CloseIdleConnections()
	}
}

var errProxyURL = errors.New("代理地址格式无效。带认证的 SOCKS5 地址可用 socks5://用户名:密码@主机:端口 或 socks5://主机:端口:用户名:密码")
var errProxyChanged = errors.New("代理配置已被修改，请刷新后重试")

type proxyConnectError struct{ status int }

func (e *proxyConnectError) Error() string {
	return fmt.Sprintf("HTTP 代理未建立 HTTPS 隧道（CONNECT %d）", e.status)
}

func parseProxyURL(value string) (*url.URL, error) {
	if len(value) > 4096 || strings.IndexFunc(value, unicode.IsControl) != -1 {
		return nil, errProxyURL
	}
	if exported := normalizeSOCKSAddress(value); exported != "" {
		value = exported
	}
	u, err := url.Parse(value)
	if err != nil || len(value) > 4096 || !oneOf(u.Scheme, "http", "https", "socks5", "socks5h") ||
		u.Hostname() == "" || strings.HasSuffix(u.Host, ":") || !oneOf(u.Path, "", "/") ||
		u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
		return nil, errProxyURL
	}
	if strings.Contains(u.Hostname(), ":") && !strings.HasPrefix(u.Host, "[") {
		return nil, errProxyURL
	}
	if port := u.Port(); port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return nil, errProxyURL
		}
	}
	return u, nil
}

// Some SOCKS services export host:port:username:password. Put those raw
// credentials into standard URL userinfo before parsing or storing the address.
func normalizeSOCKSAddress(value string) string {
	scheme, address, _ := strings.Cut(value, "://")
	scheme = strings.ToLower(scheme)
	if !oneOf(scheme, "socks5", "socks5h") || strings.Contains(address, "@") {
		return ""
	}
	hostEnd := strings.IndexByte(address, ':')
	if strings.HasPrefix(address, "[") {
		bracket := strings.Index(address, "]:")
		if bracket == -1 {
			return ""
		}
		hostEnd = bracket + 1
	}
	if hostEnd <= 0 {
		return ""
	}
	host := address[:hostEnd]
	fields := strings.SplitN(address[hostEnd+1:], ":", 3)
	if len(fields) != 3 || fields[0] == "" || fields[1] == "" || fields[2] == "" {
		return ""
	}
	if strings.IndexFunc(fields[0], func(r rune) bool { return r < '0' || r > '9' }) != -1 {
		return ""
	}
	user := url.UserPassword(fields[1], fields[2])
	return scheme + "://" + user.String() + "@" + host + ":" + fields[0]
}

func (m *egressManager) prepare(s proxySettings) (*egressTransport, *url.URL, error) {
	raw := ""
	switch s.Mode {
	case "environment":
		raw = m.envURL
	case "direct":
	case "custom":
		raw = s.URL
		if raw == "" || !oneOf(s.Scope, "all", "openai") {
			return nil, nil, errProxyURL
		}
	default:
		return nil, nil, errors.New("请选择代理模式")
	}
	var proxy *url.URL
	var err error
	if raw != "" {
		proxy, err = parseProxyURL(raw)
		if err != nil {
			return nil, nil, err
		}
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	if proxy != nil {
		transport.Proxy = func(req *http.Request) (*url.URL, error) {
			if s.Scope == "openai" && !strings.EqualFold(req.URL.Hostname(), "api.openai.com") {
				return nil, nil
			}
			return proxy, nil
		}
		transport.OnProxyConnectResponse = func(_ context.Context, _ *url.URL, _ *http.Request, response *http.Response) error {
			if response.StatusCode != http.StatusOK {
				return &proxyConnectError{status: response.StatusCode}
			}
			return nil
		}
	}
	transport.DialContext = (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext
	transport.MaxIdleConnsPerHost = 16
	result := &egressTransport{shared: transport}
	if proxy != nil {
		transport.HTTP2 = &http.HTTP2Config{SendPingTimeout: 25 * time.Second, PingTimeout: 10 * time.Second}
		transport.IdleConnTimeout = 45 * time.Second
		// Use one upstream tunnel per OpenAI image request so losing a connection
		// interrupts only that image request.
		images := transport.Clone()
		images.HTTP2 = nil
		images.ForceAttemptHTTP2 = false
		images.Protocols = new(http.Protocols)
		images.Protocols.SetHTTP1(true)
		if images.TLSClientConfig != nil {
			images.TLSClientConfig.NextProtos = []string{"http/1.1"}
		}
		images.DisableKeepAlives = true
		result.images = images
	}
	return result, proxy, nil
}

const egressPurpose = "settings\x00egress"

func loadEgress(ctx context.Context, store *store, cfg config) (*egressManager, error) {
	m := &egressManager{store: store, envURL: cfg.egressProxy, settings: proxySettings{Mode: "environment", Scope: "all"}}
	var sealed []byte
	err := store.db.QueryRowContext(ctx, "SELECT value FROM gateway_settings WHERE name='egress'").Scan(&sealed)
	if err == nil {
		data, err := store.unseal(sealed, egressPurpose)
		if err != nil || json.Unmarshal(data, &m.settings) != nil {
			return nil, errors.New("无法读取已保存的代理配置")
		}
	} else if !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	m.transport, _, err = m.prepare(m.settings)
	return m, err
}

func (m *egressManager) RoundTrip(req *http.Request) (*http.Response, error) {
	m.mu.RLock()
	transport := m.transport
	m.mu.RUnlock()
	return transport.RoundTrip(req)
}

// The browser receives only the scheme and host, even for authenticated environment proxies.
func (m *egressManager) viewLocked() object {
	s := m.settings
	raw := s.URL
	if s.Mode == "environment" {
		raw = m.envURL
	}
	address, auth := "", false
	if raw != "" {
		u, _ := parseProxyURL(raw)
		address, auth = u.Scheme+"://"+u.Host, u.User != nil
	}
	return object{"mode": s.Mode, "scope": s.Scope, "address": address, "authenticated": auth, "revision": s.Revision}
}

func (m *egressManager) view() object {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.viewLocked()
}

func (m *egressManager) candidate(input proxySettings) (proxySettings, *egressTransport, error) {
	if input.Revision != m.settings.Revision {
		return input, nil, errProxyChanged
	}
	input.URL = strings.TrimSpace(input.URL)
	if input.Mode == "custom" {
		if input.URL == "" && m.settings.Mode == "custom" {
			input.URL = m.settings.URL
		}
	} else {
		input.URL, input.Scope = "", "all"
	}
	transport, proxy, err := m.prepare(input)
	if err == nil && input.Mode == "custom" {
		input.URL = proxy.String()
	}
	return input, transport, err
}

func (m *egressManager) save(ctx context.Context, input proxySettings) (object, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	settings, transport, err := m.candidate(input)
	if err != nil {
		return nil, err
	}
	settings.Revision++
	data, err := json.Marshal(settings)
	if err != nil {
		return nil, err
	}
	_, err = m.store.db.ExecContext(ctx, `INSERT INTO gateway_settings(name,value) VALUES ('egress',?)
		ON CONFLICT(name) DO UPDATE SET value=excluded.value`, m.store.seal(data, egressPurpose))
	if err != nil {
		return nil, errors.New("无法保存代理配置")
	}
	previous := m.transport
	m.settings, m.transport = settings, transport
	previous.CloseIdleConnections()
	return m.viewLocked(), nil
}

func (a *admin) configureProxy(w http.ResponseWriter, r *http.Request) {
	var input proxySettings
	if !decodeRequest(w, r, 8<<10, &input) {
		return
	}
	view, err := a.gateway.egress.save(r.Context(), input)
	if err != nil {
		writeProxyConfigError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, view)
}

func writeProxyConfigError(w http.ResponseWriter, err error) {
	status := http.StatusBadRequest
	if errors.Is(err, errProxyChanged) {
		status = http.StatusConflict
	}
	jsonError(w, status, err.Error())
}

func (a *admin) testProxy(w http.ResponseWriter, r *http.Request) {
	var input proxySettings
	if !decodeRequest(w, r, 8<<10, &input) {
		return
	}
	m := a.gateway.egress
	m.mu.RLock()
	_, transport, err := m.candidate(input)
	m.mu.RUnlock()
	if err != nil {
		writeProxyConfigError(w, err)
		return
	}
	defer transport.CloseIdleConnections()
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, openAIBaseURL+"/models", nil)
	start := time.Now()
	resp, err := newUpstreamClient(a.gateway.cfg, transport).Do(req)
	result := object{"ok": false, "elapsedMs": time.Since(start).Milliseconds()}
	if err != nil {
		result["message"] = "连接失败，请检查代理地址、认证和服务器网络"
		var rejection *proxyConnectError
		if errors.As(err, &rejection) {
			result["message"], result["proxyStatus"] = rejection.Error(), rejection.status
		} else if errors.Is(err, context.DeadlineExceeded) {
			result["message"] = "连接超时，请检查服务器到 OpenAI 的出口"
		}
	} else {
		resp.Body.Close()
		result["upstreamStatus"] = resp.StatusCode
		if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusOK {
			result["ok"], result["message"] = true, "已连通 OpenAI 模型接口"
		} else {
			result["message"] = fmt.Sprintf("目标返回 HTTP %d，请检查出口和访问策略", resp.StatusCode)
		}
	}
	writeJSON(w, http.StatusOK, result)
}
