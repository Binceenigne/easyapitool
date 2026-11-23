package main

import (
	"bufio"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const openAIBaseURL = "https://api.openai.com/v1"

type config struct {
	listen, adminListen, pin, token, storageSecret   string
	workDir, dataDir, legacyBase, openAIKey          string
	androidReleases, windowsReleases, egressProxy    string
	publicBase, openAIPool, assetDir                 string
	imageModel, svgAuthor, svgEffort, polishModel    string
	reasoningModel, reasoningEffort                  string
	reasoningPresets                                 map[string]string
	allowedHosts                                     map[string]bool
	svgEnabled                                       bool
	trustLocalProxy                                  bool
	reserveMicroUSD                                  int64
	pricing                                          map[string]priceCard
	upstreamTimeout                                  time.Duration
	agentWorkerURL, agentWorkerToken, agentProxyBase string
	agentAttempts, imageAttempts                     int
}

func loadConfig(path string) (config, error) {
	values, err := readEnvFile(path)
	if err != nil {
		return config{}, err
	}
	get := func(name, fallback string) string {
		if value, ok := os.LookupEnv(name); ok {
			return strings.TrimSpace(value)
		}
		if value, ok := values[name]; ok {
			return value
		}
		return fallback
	}
	c := config{
		listen:          get("MIRRA_LISTEN", net.JoinHostPort(get("API_TOOLS_SERVICE_HOST", "127.0.0.1"), get("API_TOOLS_SERVICE_PORT", "8766"))),
		adminListen:     get("MIRRA_ADMIN_LISTEN", "127.0.0.1:8771"),
		pin:             get("MIRRA_ADMIN_PIN", "362514"),
		token:           get("API_TOOLS_SERVICE_TOKEN", ""),
		storageSecret:   get("MIRRA_STORAGE_SECRET", ""),
		workDir:         get("API_TOOLS_WORK_DIR", filepath.Join(os.TempDir(), "mirra")),
		dataDir:         get("MIRRA_DATA_DIR", "data"),
		legacyBase:      get("OPENAI_BASE_URL", "https://work.easyclin.cn/v1"),
		openAIKey:       get("OPENAI_API_KEY", ""),
		androidReleases: get("API_TOOLS_ANDROID_RELEASE_DIR", ""),
		windowsReleases: get("API_TOOLS_WINDOWS_RELEASE_DIR", ""),
		egressProxy:     get("MIRRA_EGRESS_PROXY", ""),
		publicBase:      strings.TrimRight(get("MIRRA_PUBLIC_OPENAI_BASE_URL", "https://39.105.74.222/openai/v1"), "/"),
		openAIPool:      get("MIRRA_OPENAI_POOL", "openai"),
		imageModel:      get("MIRRA_IMAGE_MODEL", "gpt-image-2.6-flare"),
		svgAuthor:       get("MIRRA_SVG_AUTHOR_MODEL", ""),
		svgEffort:       get("MIRRA_SVG_REASONING_EFFORT", ""),
		polishModel:     get("MIRRA_PROMPT_POLISH_MODEL", ""),
		reasoningModel:  get("MIRRA_REASONING_MODEL", ""),
		reasoningEffort: get("MIRRA_REASONING_EFFORT", ""),
		reasoningPresets: map[string]string{
			"luna":  get("MIRRA_REASONING_LUNA_MODEL", ""),
			"terra": get("MIRRA_REASONING_TERRA_MODEL", ""),
			"sol":   get("MIRRA_REASONING_SOL_MODEL", ""),
		},
		allowedHosts: map[string]bool{"api.openai.com": true},
	}
	c.assetDir = get("MIRRA_ASSET_DIR", c.workDir)
	c.agentWorkerURL = strings.TrimRight(get("MIRRA_AGENT_WORKER_URL", ""), "/")
	c.agentWorkerToken = get("MIRRA_AGENT_WORKER_TOKEN", "")
	c.agentProxyBase = strings.TrimRight(get("MIRRA_AGENT_PROXY_BASE_URL", ""), "/")
	if c.agentWorkerURL != "" {
		if !loopbackHTTPURL(c.agentWorkerURL) || len(c.agentWorkerToken) < 32 {
			return c, fmt.Errorf("MIRRA_AGENT_WORKER_URL must be loopback HTTP and MIRRA_AGENT_WORKER_TOKEN must have at least 32 characters")
		}
		if c.agentProxyBase == "" {
			_, port, splitErr := net.SplitHostPort(c.listen)
			if splitErr != nil {
				return c, fmt.Errorf("invalid MIRRA_LISTEN for Agent SDK proxy")
			}
			c.agentProxyBase = "http://127.0.0.1:" + port + "/internal/agent/v1"
		}
		if !loopbackHTTPURL(c.agentProxyBase) || !strings.HasSuffix(c.agentProxyBase, "/internal/agent/v1") {
			return c, fmt.Errorf("MIRRA_AGENT_PROXY_BASE_URL must use the loopback internal Agent proxy")
		}
	}
	var attemptsErr error
	c.agentAttempts, attemptsErr = strconv.Atoi(get("MIRRA_AGENT_MAX_ATTEMPTS", "3"))
	if attemptsErr != nil || c.agentAttempts < 1 || c.agentAttempts > 5 {
		return c, fmt.Errorf("MIRRA_AGENT_MAX_ATTEMPTS must be 1–5")
	}
	c.imageAttempts, attemptsErr = strconv.Atoi(get("MIRRA_IMAGE_MAX_ATTEMPTS", "3"))
	if attemptsErr != nil || c.imageAttempts < 1 || c.imageAttempts > 5 {
		return c, fmt.Errorf("MIRRA_IMAGE_MAX_ATTEMPTS must be 1–5")
	}
	c.legacyBase = strings.TrimRight(c.legacyBase, "/")
	if c.storageSecret == "" {
		c.storageSecret = c.token
	}
	if len(c.token) < 32 || len(c.storageSecret) < 32 {
		return c, fmt.Errorf("API_TOOLS_SERVICE_TOKEN and MIRRA_STORAGE_SECRET must have at least 32 characters")
	}
	if len(c.pin) < 6 || len(c.pin) > 64 || strings.IndexFunc(c.pin, func(r rune) bool { return r < '0' || r > '9' }) >= 0 {
		return c, fmt.Errorf("MIRRA_ADMIN_PIN must contain 6–64 digits")
	}
	host, _, err := net.SplitHostPort(c.adminListen)
	if err != nil || !net.ParseIP(host).IsLoopback() {
		return c, fmt.Errorf("MIRRA_ADMIN_LISTEN must bind to a loopback IP")
	}
	legacy, err := url.Parse(c.legacyBase)
	if err != nil || legacy.Hostname() == "" {
		return c, fmt.Errorf("invalid OPENAI_BASE_URL")
	}
	c.allowedHosts[strings.ToLower(legacy.Hostname())] = true
	for _, host := range strings.Split(get("MIRRA_ALLOWED_HOSTS", ""), ",") {
		host = strings.ToLower(strings.TrimSpace(host))
		if host != "" {
			c.allowedHosts[host] = true
		}
	}
	if _, err := c.baseURL(c.legacyBase); err != nil {
		return c, err
	}
	if c.egressProxy != "" {
		if _, err := parseProxyURL(c.egressProxy); err != nil {
			return c, fmt.Errorf("invalid MIRRA_EGRESS_PROXY")
		}
	}
	c.svgEnabled, err = strconv.ParseBool(get("MIRRA_SVG_ENABLED", "false"))
	if err != nil {
		return c, fmt.Errorf("invalid MIRRA_SVG_ENABLED")
	}
	c.trustLocalProxy, err = strconv.ParseBool(get("MIRRA_TRUST_LOCAL_PROXY", "false"))
	if err != nil {
		return c, fmt.Errorf("invalid MIRRA_TRUST_LOCAL_PROXY")
	}
	if file := get("MIRRA_PRICING_FILE", ""); file != "" {
		c.pricing, err = loadPricing(file)
		if err != nil {
			return c, err
		}
	}
	c.reserveMicroUSD, err = parseMicroUSD(get("MIRRA_USD_RESERVE_PER_REQUEST", "0"))
	if err != nil {
		return c, fmt.Errorf("invalid MIRRA_USD_RESERVE_PER_REQUEST")
	}
	c.upstreamTimeout, err = time.ParseDuration(get("MIRRA_UPSTREAM_TIMEOUT", "12m"))
	if err != nil || c.upstreamTimeout < time.Second || c.upstreamTimeout > time.Hour {
		return c, fmt.Errorf("invalid MIRRA_UPSTREAM_TIMEOUT")
	}
	for _, effort := range []string{c.svgEffort, c.reasoningEffort} {
		if effort != "" && !oneOf(effort, "none", "minimal", "low", "medium", "high", "xhigh") {
			return c, fmt.Errorf("invalid configured reasoning effort")
		}
	}
	return c, nil
}

func readEnvFile(path string) (map[string]string, error) {
	values := make(map[string]string)
	file, err := os.Open(path)
	if os.IsNotExist(err) && path == ".env" {
		return values, nil
	}
	if err != nil {
		return nil, err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for lineNumber := 1; scanner.Scan(); lineNumber++ {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok || strings.TrimSpace(key) == "" {
			return nil, fmt.Errorf("invalid .env entry on line %d", lineNumber)
		}
		value = strings.TrimSpace(value)
		if len(value) >= 2 && (value[0] == '"' || value[0] == '\'') && value[0] == value[len(value)-1] {
			value = value[1 : len(value)-1]
		}
		values[strings.TrimSpace(key)] = value
	}
	return values, scanner.Err()
}

func (c config) baseURL(raw string) (*url.URL, error) {
	u, err := url.Parse(strings.TrimRight(strings.TrimSpace(raw), "/"))
	if err != nil || u.Scheme != "https" || !c.allowedHosts[strings.ToLower(u.Hostname())] ||
		u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" ||
		strings.Contains(u.Path, "..") || strings.Contains(u.Path, "\\") {
		return nil, fmt.Errorf("上游地址必须是已允许的 HTTPS 地址")
	}
	return u, nil
}

func oneOf(value string, choices ...string) bool {
	for _, choice := range choices {
		if choice == value {
			return true
		}
	}
	return false
}
