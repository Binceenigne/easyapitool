package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/jpeg"
	"image/png"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"golang.org/x/image/draw"
)

type agentVisualScopeKey struct{}
type agentLeaseContextKey struct{}

var visualReservedNetworks = []netip.Prefix{netip.MustParsePrefix("100.64.0.0/10"), netip.MustParsePrefix("192.0.0.0/24"), netip.MustParsePrefix("192.0.2.0/24"), netip.MustParsePrefix("198.18.0.0/15"), netip.MustParsePrefix("198.51.100.0/24"), netip.MustParsePrefix("203.0.113.0/24"), netip.MustParsePrefix("2001:db8::/32")}

// A scope belongs to one device task. Only genuine image_result items seen on
// the metered provider response can enter its URL allowlist.
type agentVisualScope struct {
	mu           sync.Mutex
	mobile       *mobile
	id           credentialID
	options      imageOptions
	uploaded     int
	initial      []asset
	allowed      map[string]object
	order        []string
	inspected    map[string]object
	assets       map[string]asset
	selected     []string
	sources      []any
	stats        object
	planning     object
	mode, effort string
	turns        []any
	lookup       func(context.Context, string) ([]net.IPAddr, error)
}

func visualPreview(decoded image.Image) (string, error) {
	width, height := decoded.Bounds().Dx(), decoded.Bounds().Dy()
	if long := max(width, height); long > 384 {
		width, height = max(1, width*384/long), max(1, height*384/long)
	}
	small := image.NewNRGBA(image.Rect(0, 0, width, height))
	draw.ApproxBiLinear.Scale(small, small.Bounds(), decoded, decoded.Bounds(), draw.Src, nil)
	var thumbnail bytes.Buffer
	if jpeg.Encode(&thumbnail, small, &jpeg.Options{Quality: 65}) != nil || thumbnail.Len() > 96<<10 {
		return "", errors.New("参考图预览超过大小限制")
	}
	return "data:image/jpeg;base64," + base64.StdEncoding.EncodeToString(thumbnail.Bytes()), nil
}
func (s *agentVisualScope) initialInput(prompt string) (any, error) {
	if len(s.initial) == 0 {
		return prompt, nil
	}
	content := []object{{"type": "input_text", "text": prompt}}
	for _, reference := range s.initial {
		if reference.owner != s.id.owner {
			return nil, errors.New("参考图片不属于当前设备")
		}
		file, err := os.Open(reference.path)
		if err != nil {
			return nil, err
		}
		dimensions, _, err := image.DecodeConfig(file)
		if err != nil || int64(dimensions.Width)*int64(dimensions.Height) > 24_000_000 {
			file.Close()
			return nil, errors.New("参考图片超过可分析的像素上限")
		}
		file.Seek(0, io.SeekStart)
		decoded, _, err := image.Decode(file)
		file.Close()
		if err != nil {
			return nil, errors.New("参考图片解码失败")
		}
		preview, err := visualPreview(decoded)
		if err != nil {
			return nil, err
		}
		content = append(content, object{"type": "input_image", "image_url": preview})
	}
	return []object{{"role": "user", "content": content}}, nil
}

func newAgentVisualScope(m *mobile, id credentialID, o imageOptions, uploaded int) *agentVisualScope {
	return &agentVisualScope{mobile: m, id: id, options: o, uploaded: uploaded, allowed: map[string]object{}, inspected: map[string]object{}, assets: map[string]asset{}, lookup: net.DefaultResolver.LookupIPAddr}
}
func visualCandidateID(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return "webref-" + hex.EncodeToString(sum[:])[:12]
}
func visualPlanningBudget(mode, effort string) (int, int) {
	if mode == "advanced" {
		mode = map[string]string{"low": "flash", "medium": "medium", "high": "high", "xhigh": "extra", "max": "max"}[effort]
	}
	switch mode {
	case "flash":
		return 3, 3
	case "medium":
		return 5, 4
	case "high":
		return 7, 6
	case "extra":
		return 10, 6
	case "max":
		return 12, 8
	case "instant", "continuation":
		return 1, 0
	}
	return 12, 8
}
func (s *agentVisualScope) limits(key accessKey) (int, int, error) {
	limit := 16
	if key.ID != "" {
		profile, err := s.mobile.g.modelsForKey(key)
		if err != nil {
			return 0, 0, err
		}
		if !profile.Tier.WebSearch || !profile.Tier.ImageEditing {
			return 0, 0, nil
		}
		limit = profile.Tier.MaxReferences
	}
	inspect := min(12, max(0, limit-s.uploaded))
	_, modelReferences := visualPlanningBudget(s.mode, s.effort)
	adopt := min(modelReferences, inspect)
	if s.options.format == "svg" {
		adopt = 0
	}
	return inspect, adopt, nil
}

func arrayObjects(value any) []object {
	result := []object{}
	switch values := value.(type) {
	case []any:
		for _, value := range values {
			if item := asObject(value); item != nil {
				result = append(result, item)
			}
		}
	case []object:
		result = values
	}
	return result
}

func (s *agentVisualScope) registerResponse(response object) {
	if status := objectString(response, "status"); status != "" && status != "completed" {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, call := range arrayObjects(response["output"]) {
		if objectString(call, "type") != "web_search_call" {
			continue
		}
		for _, result := range arrayObjects(call["results"]) {
			if objectString(result, "type") != "image_result" {
				continue
			}
			raw := objectString(result, "image_url")
			if raw == "" || len(raw) > 4096 {
				continue
			}
			if _, exists := s.allowed[raw]; exists {
				continue
			}
			if len(s.allowed) >= 48 {
				continue
			}
			s.allowed[raw] = object{"id": visualCandidateID(raw), "imageUrl": raw, "thumbnailUrl": objectString(result, "thumbnail_url"), "sourceUrl": objectString(result, "source_website_url"), "caption": objectString(result, "caption"), "title": objectString(result, "title")}
			s.order = append(s.order, raw)
		}
	}
}

type visualResponseObserver struct {
	scope    *agentVisualScope
	stream   bool
	buffer   []byte
	overflow bool
}

func (o *visualResponseObserver) Write(p []byte) (int, error) {
	n := len(p)
	if o.stream {
		for len(p) > 0 {
			part, rest, found := bytes.Cut(p, []byte{'\n'})
			p = rest
			if !o.overflow {
				if len(o.buffer)+len(part) > 16<<20 {
					o.overflow = true
					o.buffer = nil
				} else {
					o.buffer = append(o.buffer, part...)
				}
			}
			if !found {
				break
			}
			if !o.overflow {
				line := bytes.TrimSpace(o.buffer)
				if bytes.HasPrefix(line, []byte("data:")) {
					var event object
					if json.Unmarshal(bytes.TrimSpace(line[5:]), &event) == nil && objectString(event, "type") == "response.completed" {
						o.scope.registerResponse(asObject(event["response"]))
					}
				}
			}
			o.buffer = nil
			o.overflow = false
		}
	} else if !o.overflow {
		if len(o.buffer)+len(p) > 16<<20 {
			o.overflow = true
			o.buffer = nil
		} else {
			o.buffer = append(o.buffer, p...)
		}
	}
	return n, nil
}
func (o *visualResponseObserver) finish() {
	if !o.stream && !o.overflow {
		var response object
		if json.Unmarshal(o.buffer, &response) == nil {
			o.scope.registerResponse(response)
		}
	}
}

func (s *agentVisualScope) publicURL(ctx context.Context, raw string) error {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" || len(raw) > 4096 || (parsed.Port() != "" && parsed.Port() != "443") {
		return errors.New("参考图片仅允许公共 HTTPS 地址")
	}
	addresses, err := s.lookup(ctx, parsed.Hostname())
	if err != nil || len(addresses) == 0 {
		return errors.New("无法解析参考图片地址")
	}
	for _, address := range addresses {
		ip, ok := netip.AddrFromSlice(address.IP)
		if !ok {
			return errors.New("参考图片地址无效")
		}
		ip = ip.Unmap()
		for _, reserved := range visualReservedNetworks {
			if reserved.Contains(ip) {
				return errors.New("参考图片地址不能指向保留网络")
			}
		}
		if !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified() || ip.IsMulticast() {
			return errors.New("参考图片地址不能指向内网或本机")
		}
	}
	return nil
}

func (s *agentVisualScope) fetchImage(ctx context.Context, raw string) (asset, string, error) {
	var original asset
	client := *s.mobile.g.client
	client.Timeout = 15 * time.Second
	client.CheckRedirect = func(request *http.Request, via []*http.Request) error {
		if len(via) >= 4 {
			return errors.New("参考图片重定向过多")
		}
		return s.publicURL(request.Context(), request.URL.String())
	}
	if err := s.publicURL(ctx, raw); err != nil {
		return original, "", err
	}
	request, err := http.NewRequestWithContext(ctx, "GET", raw, nil)
	if err != nil {
		return original, "", err
	}
	request.Header.Set("Accept", "image/png,image/jpeg,image/webp,image/gif")
	request.Header.Set("User-Agent", "Mirra-Visual-Reference/1.0")
	response, err := client.Do(request)
	if err != nil {
		return original, "", errors.New("参考图片下载失败")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return original, "", fmt.Errorf("参考图片下载返回 HTTP %d", response.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, 12<<20+1))
	if err != nil || len(data) == 0 || len(data) > 12<<20 {
		return original, "", errors.New("参考图片为空、下载中断或超过12MB")
	}
	config, format, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || !oneOf(format, "png", "jpeg", "webp", "gif") || config.Width < 32 || config.Height < 32 || int64(config.Width)*int64(config.Height) > 9_000_000 {
		return original, "", errors.New("参考图片格式或像素尺寸无效")
	}
	decoded, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return original, "", errors.New("参考图片解码失败")
	}
	width, height := config.Width, config.Height
	if long := max(width, height); long > 1536 {
		width, height = max(1, width*1536/long), max(1, height*1536/long)
		small := image.NewNRGBA(image.Rect(0, 0, width, height))
		draw.ApproxBiLinear.Scale(small, small.Bounds(), decoded, decoded.Bounds(), draw.Src, nil)
		decoded = small
	}
	var raster bytes.Buffer
	if png.Encode(&raster, decoded) != nil {
		return original, "", errors.New("无法规范化参考图片")
	}
	original, err = s.mobile.saveAsset(s.id.owner, bytes.NewReader(raster.Bytes()), false, false)
	if err != nil {
		return original, "", err
	}
	preview, err := visualPreview(decoded)
	return original, preview, err
}

func (s *agentVisualScope) inspect(ctx context.Context, key accessKey, urls []string) (object, error) {
	limit, _, err := s.limits(key)
	if err != nil {
		return nil, err
	}
	if limit == 0 {
		return nil, denied("参考图检视或图片分析")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(urls) == 0 {
		urls = append([]string(nil), s.order...)
	}
	if len(urls) > 12 {
		urls = urls[:12]
	}
	for _, raw := range urls {
		if s.allowed[raw] == nil {
			return nil, errors.New("只能检视本次 OpenAI 图片搜索返回的图片")
		}
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	candidates := []object{}
	failures := []object{}
	for _, raw := range urls {
		source := s.allowed[raw]
		id := objectString(source, "id")
		if known := s.inspected[id]; known != nil {
			copy := object{}
			for k, v := range known {
				if k != "previewDataUrl" {
					copy[k] = v
				}
			}
			copy["alreadyInspected"] = true
			candidates = append(candidates, copy)
			continue
		}
		if len(s.inspected) >= limit {
			break
		}
		original, preview, err := s.fetchImage(ctx, raw)
		downloadedURL := raw
		if err != nil && ctx.Err() == nil {
			thumbnail := objectString(source, "thumbnailUrl")
			if thumbnail != "" && thumbnail != raw {
				primaryError := err
				original, preview, err = s.fetchImage(ctx, thumbnail)
				if err != nil {
					err = fmt.Errorf("原图不可用（%v），缩略图也不可用（%w）", primaryError, err)
				} else {
					downloadedURL = thumbnail
				}
			}
		}
		if err != nil {
			failures = append(failures, object{"id": id, "imageUrl": raw, "error": err.Error()})
			continue
		}
		item := object{}
		for k, v := range source {
			item[k] = v
		}
		for k, v := range assetInfo(original, original) {
			item[k] = v
		}
		item["previewDataUrl"], item["width"], item["height"] = preview, original.width, original.height
		item["actualDownloadedUrl"] = downloadedURL
		s.inspected[id], s.assets[id] = item, original
		s.mobile.mu.Lock()
		held := s.mobile.assets[original.id]
		held.holds++
		s.mobile.assets[original.id] = held
		s.mobile.mu.Unlock()
		candidates = append(candidates, item)
	}
	return object{"candidates": candidates, "failures": failures, "inspectedCount": len(s.inspected), "maxReferences": limit}, nil
}

func (s *agentVisualScope) selectReferences(key accessKey, ids []string, rationale string) (object, error) {
	_, limit, err := s.limits(key)
	if err != nil {
		return nil, err
	}
	if len(ids) > limit {
		return nil, denied("采用的网络参考图数量")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	seen := map[string]bool{}
	selected := []string{}
	for _, id := range ids {
		if s.inspected[id] == nil {
			return nil, errors.New("只能采用已成功检视的图片")
		}
		if !seen[id] {
			seen[id] = true
			selected = append(selected, id)
		}
	}
	s.selected = selected
	references := s.referencesLocked()
	return object{"selectedIds": selected, "webReferences": references, "rationale": rationale}, nil
}

func numberInt(value any) int {
	switch n := value.(type) {
	case int:
		return n
	case float64:
		return int(n)
	}
	return 0
}
func (s *agentVisualScope) recordEvent(event object) {
	s.mu.Lock()
	defer s.mu.Unlock()
	kind := objectString(event, "type")
	if kind == "result" || kind == "error" {
		s.stats = object{}
		for _, name := range []string{"turnCount", "requestCount", "toolCallCount", "webSearchCallCount", "localToolCallCount"} {
			if value, ok := event[name]; ok {
				s.stats[name] = value
			}
		}
		if len(s.stats) == 0 {
			if usage := asObject(event["usage"]); usage != nil {
				s.stats["requestCount"] = usage["requests"]
			}
		}
		if values, ok := event["sources"].([]any); ok {
			s.sources = values
		}
		for _, raw := range s.turns {
			turn := asObject(raw)
			if turn["status"] == "running" {
				turn["status"] = "completed"
				if kind == "error" {
					turn["status"] = "failed"
				}
			}
		}
		return
	}
	n := numberInt(event["turn"])
	if n < 1 {
		return
	}
	var turn object
	for _, value := range s.turns {
		item := asObject(value)
		if numberInt(item["turn"]) == n {
			turn = item
			break
		}
	}
	if turn == nil {
		turn = object{"turn": n, "status": "running", "title": "正在完善创作方案", "text": "", "tools": []any{}}
		s.turns = append(s.turns, turn)
	}
	switch kind {
	case "turn_started":
		if event["reset"] == true {
			turn["text"], turn["tools"] = "", []any{}
		}
		turn["status"] = "running"
	case "text_delta":
		turn["text"] = objectString(turn, "text") + objectString(event, "delta")
	case "turn_completed":
		turn["status"] = "completed"
	case "tool_started", "tool_completed", "tool_failed", "web_search_started", "web_search_completed", "web_search_failed":
		tools, _ := turn["tools"].([]any)
		id := objectString(event, "callId")
		if id == "" {
			return
		}
		var tool object
		for _, raw := range tools {
			candidate := asObject(raw)
			if objectString(candidate, "callId") == id {
				tool = candidate
				break
			}
		}
		if tool == nil {
			tool = object{"callId": id}
			tools = append(tools, tool)
			turn["tools"] = tools
		}
		for _, name := range []string{"tool", "arguments", "query", "queries", "action", "sources", "resultCount"} {
			if value, ok := event[name]; ok {
				tool[name] = value
			}
		}
		if strings.HasPrefix(kind, "web_search_") {
			tool["tool"] = "web_search"
		}
		tool["status"] = "completed"
		if strings.HasSuffix(kind, "started") {
			tool["status"] = "running"
		}
		if strings.HasSuffix(kind, "failed") {
			tool["status"] = "failed"
		}
	}
}
func (s *agentVisualScope) referencesLocked() []object {
	refs := []object{}
	for _, id := range s.selected {
		info := object{}
		for key, value := range s.inspected[id] {
			if key != "previewDataUrl" {
				info[key] = value
			}
		}
		info["provider"] = "OpenAI"
		refs = append(refs, info)
	}
	return refs
}
func (s *agentVisualScope) references() []object {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.referencesLocked()
}
func (s *agentVisualScope) adopted() []asset {
	s.mu.Lock()
	defer s.mu.Unlock()
	items := []asset{}
	for _, id := range s.selected {
		items = append(items, s.assets[id])
	}
	return items
}
func (s *agentVisualScope) release() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.mobile.mu.Lock()
	defer s.mobile.mu.Unlock()
	for _, a := range s.assets {
		item := s.mobile.assets[a.id]
		item.holds = max(0, item.holds-1)
		s.mobile.assets[a.id] = item
	}
}

func (g *gateway) agentVisual(w http.ResponseWriter, r *http.Request) {
	lease, selected, ok := g.authorizeAgentLease(w, r)
	if !ok {
		return
	}
	scope, _ := lease.ctx.Value(agentVisualScopeKey{}).(*agentVisualScope)
	if !lease.web || scope == nil {
		openAIError(w, 403, "agent_lease_scope", "本次调用未开放视觉参考工具")
		return
	}
	if err := g.checkReasoningTier(selected.key, lease.model, lease.effort, lease.mode, true); err != nil {
		g.proxyError(w, err)
		return
	}
	var input struct {
		Operation    string   `json:"operation"`
		ImageURLs    []string `json:"image_urls"`
		CandidateIDs []string `json:"candidate_ids"`
		Rationale    string   `json:"rationale"`
	}
	if !decodeRequest(w, r, 64<<10, &input) {
		return
	}
	ctx, cancel := context.WithCancel(r.Context())
	stop := context.AfterFunc(lease.ctx, cancel)
	defer stop()
	defer cancel()
	var result object
	var err error
	switch input.Operation {
	case "inspect":
		result, err = scope.inspect(ctx, selected.key, input.ImageURLs)
	case "select":
		result, err = scope.selectReferences(selected.key, input.CandidateIDs, input.Rationale)
	default:
		err = errors.New("视觉参考操作无效")
	}
	if err != nil {
		openAIError(w, 403, "visual_reference_rejected", err.Error())
		return
	}
	if input.Operation == "select" {
		scope.mobile.publish(scope.id, object{"type": "react_visual_selected", "webReferences": result["webReferences"], "webReferenceCount": len(scope.references()), "rationale": input.Rationale}, false)
	}
	writeJSON(w, 200, result)
}

// Kept local to the metered response path; never trusts client supplied URLs.
func visualObserverForRequest(r *http.Request, stream bool) *visualResponseObserver {
	lease, _ := r.Context().Value(agentLeaseContextKey{}).(*agentProxyLease)
	if lease == nil {
		return nil
	}
	scope, _ := lease.ctx.Value(agentVisualScopeKey{}).(*agentVisualScope)
	if scope == nil {
		return nil
	}
	return &visualResponseObserver{scope: scope, stream: stream}
}
