package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
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
	transport *http.Transport
}

var errProxyURL = errors.New("请填写有效的 HTTP、HTTPS、SOCKS5 或 SOCKS5H 代理地址，可包含认证信息")
var errProxyChanged = errors.New("代理配置已被修改，请刷新后重试")

func parseProxyURL(value string) (*url.URL, error) {
	u, err := url.Parse(value)
	if err != nil || len(value) > 4096 || !oneOf(u.Scheme, "http", "https", "socks5", "socks5h") ||
		u.Hostname() == "" || strings.HasSuffix(u.Host, ":") || !oneOf(u.Path, "", "/") ||
		u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
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

func (m *egressManager) prepare(s proxySettings) (*http.Transport, error) {
	raw := ""
	switch s.Mode {
	case "environment":
		raw = m.envURL
	case "direct":
	case "custom":
		raw = s.URL
		if raw == "" || !oneOf(s.Scope, "all", "openai") {
			return nil, errProxyURL
		}
	default:
		return nil, errors.New("请选择代理模式")
	}
	var proxy *url.URL
	var err error
	if raw != "" {
		proxy, err = parseProxyURL(raw)
		if err != nil {
			return nil, err
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
	}
	transport.DialContext = (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext
	transport.MaxIdleConnsPerHost = 16
	return transport, nil
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
	m.transport, err = m.prepare(m.settings)
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

func (m *egressManager) candidate(input proxySettings) (proxySettings, *http.Transport, error) {
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
	transport, err := m.prepare(input)
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
		if errors.Is(err, context.DeadlineExceeded) {
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
