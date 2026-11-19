package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/netip"
	"net/textproto"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

const maxImageEnvelope = (maxImageBytes*4/3 + 1<<20)

type imageOptions struct {
	model, size, ratio, quality, complexity, format, background string
	svg                                                         modelPreset
	compression                                                 *int
	partial, n                                                  int
	stream                                                      bool
}

var modelPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)

var imageSizes = map[string]string{
	"1:1": "1024x1024", "4:3": "1024x768", "3:4": "768x1024", "16:9": "1280x720", "9:16": "720x1280",
	"21:9": "1680x720", "2:3": "1024x1536", "3:2": "1536x1024",
}

func (g *gateway) imageOptions(options object, edit bool, settings *modelSettings) (imageOptions, error) {
	defaultModel := g.cfg.imageModel
	svg := svgSettings{Enabled: g.cfg.svgEnabled, modelPreset: modelPreset{Model: g.cfg.svgAuthor, Effort: g.cfg.svgEffort}}
	if settings != nil {
		defaultModel, svg = settings.DefaultImage, settings.SVG
	}
	o := imageOptions{model: objectString(options, "imageModel"), size: objectString(options, "size"), ratio: objectString(options, "aspectRatio"),
		quality: objectString(options, "quality"), complexity: objectString(options, "complexity"), format: objectString(options, "output_format"),
		background: objectString(options, "background"), stream: true, partial: 3, n: 1}
	if o.model == "" {
		o.model = defaultModel
	} else if settings != nil && settings.Revision > 0 && !slices.Contains(settings.ImageModels, o.model) {
		return o, errors.New("网关未开放此生图模型，请刷新客户端后重新选择")
	}
	if o.ratio == "" {
		o.ratio = "auto"
	}
	if o.size == "" || o.size == "auto" {
		o.size = imageSizes[o.ratio]
		if o.size == "" {
			o.size = "auto"
		}
	}
	if o.quality == "" {
		o.quality = "auto"
	}
	if o.background == "" {
		o.background = objectString(options, "transparency")
		if o.background == "" {
			o.background = "auto"
		}
	}
	if o.format == "" {
		o.format = "png"
		if o.background != "transparent" {
			if compression, ok := map[string]int{"large": 90, "medium": 75, "small": 55}[objectString(options, "outputPreset")]; ok {
				o.format, o.compression = "jpeg", &compression
			}
		}
	}
	if !modelPattern.MatchString(o.model) || !validImageSize(o.size) || !oneOf(o.quality, "auto", "low", "medium", "high", "xhigh", "max") ||
		!oneOf(o.background, "auto", "opaque", "transparent") || !oneOf(o.complexity, "", "low", "medium", "high") || imageMIME[o.format] == "" {
		return o, errors.New("生图模型、大小、质量、复杂度、背景或输出格式无效")
	}
	if o.background == "transparent" && !oneOf(o.format, "png", "webp", "svg") {
		return o, errors.New("透明背景需要 PNG、WebP 或 SVG")
	}
	if o.format == "svg" && (!svg.Enabled || edit) {
		return o, errors.New("SVG 需要网关启用，且仅支持无参考图的生成")
	}
	if options["imageCount"] != nil {
		n, ok := tokenCount(options["imageCount"])
		if !ok || n < 1 || n > 10 || (o.format == "svg" && n != 1) {
			return o, errors.New("图片数量无效，SVG 仅支持生成一张")
		}
		o.n = int(n)
	}
	if stream, ok := options["stream"].(bool); ok {
		o.stream = stream
	}
	if o.format == "svg" || o.format == "gif" {
		o.stream = false
	}
	o.svg = svg.modelPreset
	if options["partial_images"] != nil {
		n, ok := tokenCount(options["partial_images"])
		if !ok || n > 3 {
			return o, errors.New("预览帧数量须为 0 至 3")
		}
		o.partial = int(n)
	}
	if options["output_compression"] != nil {
		n, ok := tokenCount(options["output_compression"])
		if !ok || n > 100 || !oneOf(o.format, "webp", "jpeg") {
			return o, errors.New("压缩质量仅适用于 JPEG 和 WebP，范围为 0 至 100")
		}
		v := int(n)
		o.compression = &v
	}
	return o, nil
}

func validImageSize(size string) bool {
	if size == "auto" {
		return true
	}
	width, height, ok := strings.Cut(size, "x")
	w, errW := strconv.Atoi(width)
	h, errH := strconv.Atoi(height)
	if !ok || errW != nil || errH != nil || w <= 0 || h <= 0 || w > 3840 || h > 3840 || w%16 != 0 || h%16 != 0 {
		return false
	}
	return max(w, h) <= 3*min(w, h) && w*h >= 655360 && w*h <= 8294400 && fmt.Sprintf("%dx%d", w, h) == size
}

func (m *mobile) renderImage(ctx context.Context, id credentialID, key keyInput, ip netip.Addr, prompt string, o imageOptions, refs []asset, index int) (object, error) {
	selected, err := m.resolveKey(ctx, key, ip, true)
	if err != nil {
		return nil, err
	}
	model := o.model
	if model == "gpt-image-2.5" && selected.upstream.URL == openAIBaseURL {
		model = "gpt-image-2.5-flare"
	}
	fields := object{"model": model, "prompt": prompt, "size": o.size, "quality": o.quality, "output_format": o.format,
		"background": o.background, "n": 1}
	if selected.upstream.URL == m.g.cfg.legacyBase {
		fields["moderation"] = "low"
	}
	if o.complexity != "" {
		fields["complexity"] = o.complexity
	}
	if o.compression != nil {
		fields["output_compression"] = *o.compression
	}
	if o.stream {
		fields["stream"], fields["partial_images"] = true, o.partial
	}
	if o.format == "svg" {
		svg := object{}
		if o.svg.Model != "" {
			svg["model"] = o.svg.Model
		}
		if o.svg.Effort != "" {
			svg["reasoning"] = object{"effort": o.svg.Effort}
		}
		if len(svg) != 0 {
			fields["svg"] = svg
		}
	}
	path, contentType := "/images/generations", "application/json"
	var body io.Reader
	var length int64
	if len(refs) > 0 {
		path = "/images/edits"
		parts, err := imageMultipart(fields, refs)
		if err != nil {
			return nil, errors.New("无法读取参考图片")
		}
		defer parts.close()
		body, contentType, length = parts.reader, parts.contentType, parts.length
	} else {
		encoded, err := json.Marshal(fields)
		if err != nil {
			return nil, err
		}
		body, length = bytes.NewReader(encoded), int64(len(encoded))
	}
	request, err := m.g.upstreamRequest(ctx, selected.upstream, http.MethodPost, path, "", body)
	if err != nil {
		return nil, err
	}
	request.ContentLength = length
	request.Header.Set("Content-Type", contentType)
	if o.stream {
		request.Header.Set("Accept", "text/event-stream, application/json")
	}
	reservation, err := m.g.reserve(ctx, selected)
	if err != nil {
		return nil, err
	}
	status := 0
	var count *meter
	defer func() { m.g.recordPriced(reservation, status, count, model, o.format == "svg") }()
	response, err := m.g.client.Do(request)
	if err != nil {
		status = http.StatusBadGateway
		return nil, m.g.connectionError(selected.upstream, err)
	}
	defer response.Body.Close()
	status = response.StatusCode
	if err := upstreamStatus(response); err != nil {
		return nil, err
	}
	original, metered, err := m.readImageResponse(response, id, index, o.partial, o.format == "svg")
	count = metered
	if err != nil {
		return nil, err
	}
	preview := m.preview(original)
	item := assetInfo(original, preview)
	item["ok"], item["itemIndex"], item["actualSize"], item["quality"] = true, index, fmt.Sprintf("%dx%d", original.width, original.height), o.quality
	if count != nil {
		item["usage"], item["svgUsage"] = count.Detail["usage"], count.Detail["svg_usage"]
	}
	return item, nil
}

type multipartBody struct {
	reader      io.Reader
	contentType string
	length      int64
	files       []*os.File
}

func imageMultipart(fields object, references []asset) (multipartBody, error) {
	var body multipartBody
	var header bytes.Buffer
	writer := multipart.NewWriter(&header)
	body.contentType = writer.FormDataContentType()
	for key, value := range fields {
		if err := writer.WriteField(key, fmt.Sprint(value)); err != nil {
			return body, err
		}
	}
	readers := []io.Reader{}
	flushHeader := func() {
		readers = append(readers, bytes.NewReader(bytes.Clone(header.Bytes())))
		body.length += int64(header.Len())
		header.Reset()
	}
	for index, a := range references {
		part := make(textproto.MIMEHeader)
		part.Set("Content-Disposition", fmt.Sprintf(`form-data; name="image[]"; filename="reference-%d%s"`, index+1, extension(a.format)))
		part.Set("Content-Type", a.contentType)
		if _, err := writer.CreatePart(part); err != nil {
			body.close()
			return body, err
		}
		flushHeader()
		file, err := os.Open(a.path)
		if err != nil {
			body.close()
			return body, err
		}
		body.files = append(body.files, file)
		readers = append(readers, file)
		body.length += a.size
	}
	if err := writer.Close(); err != nil {
		body.close()
		return body, err
	}
	flushHeader()
	body.reader = io.MultiReader(readers...)
	return body, nil
}

func (p multipartBody) close() {
	for _, file := range p.files {
		file.Close()
	}
}

type imageEnvelope struct {
	Type    string
	Encoded string `json:"b64_json"`
	Partial string `json:"partial_image_b64"`
	Data    []struct {
		Encoded string `json:"b64_json"`
	}
	Usage    object
	SVGUsage object          `json:"svg_usage"`
	Err      json.RawMessage `json:"error"`
}

func (e imageEnvelope) encoded() string {
	if e.Encoded != "" {
		return e.Encoded
	}
	if len(e.Data) > 0 {
		return e.Data[0].Encoded
	}
	return ""
}

func (m *mobile) readImageResponse(response *http.Response, job credentialID, itemIndex, partialTotal int, allowSVG bool) (asset, *meter, error) {
	if !strings.HasPrefix(response.Header.Get("Content-Type"), "text/event-stream") {
		var e imageEnvelope
		if err := json.NewDecoder(io.LimitReader(response.Body, maxImageEnvelope)).Decode(&e); err != nil {
			return asset{}, nil, errors.New("上游图片响应无效或过大")
		}
		count := usageMeter(e.Usage, e.SVGUsage)
		a, err := m.decodeImage(job.owner, e.encoded(), allowSVG)
		return a, count, err
	}
	scanner := bufio.NewScanner(response.Body)
	scanner.Buffer(make([]byte, 64<<10), maxImageEnvelope)
	var last asset
	var count *meter
	partialIndex, completed := 0, false
	for scanner.Scan() {
		raw := bytes.TrimSpace(scanner.Bytes())
		if !bytes.HasPrefix(raw, []byte("data:")) {
			continue
		}
		raw = bytes.TrimSpace(raw[5:])
		if bytes.Equal(raw, []byte("[DONE]")) {
			break
		}
		var e imageEnvelope
		if json.Unmarshal(raw, &e) != nil {
			continue
		}
		if e.Type == "error" || strings.HasSuffix(e.Type, ".failed") || (len(e.Err) > 0 && string(e.Err) != "null") {
			return asset{}, count, errors.New("上游生图流报告生成失败")
		}
		if strings.HasSuffix(e.Type, ".partial_image") && !completed && partialIndex < partialTotal {
			data := e.encoded()
			if data == "" {
				data = e.Partial
			}
			a, err := m.decodeImage(job.owner, data, false)
			if err == nil {
				last = a
				partialIndex++
				event := assetInfo(a, m.preview(a))
				event["type"], event["itemIndex"], event["partialIndex"], event["partialTotal"] = "item_partial", itemIndex, partialIndex, partialTotal
				m.publish(job, event, false)
			}
		}
		if strings.HasSuffix(e.Type, ".completed") {
			completed = true
			count = usageMeter(e.Usage, e.SVGUsage)
			if encoded := e.encoded(); encoded != "" {
				a, err := m.decodeImage(job.owner, encoded, allowSVG)
				return a, count, err
			}
		}
	}
	if completed && last.id != "" {
		// Some relay streams signal completion after sending the full image in their last frame.
		return last, count, nil
	}
	return asset{}, count, errors.New("上游图片流未返回完成的图片")
}

func (m *mobile) decodeImage(owner, encoded string, allowSVG bool) (asset, error) {
	if encoded == "" {
		return asset{}, errors.New("上游未返回图片内容")
	}
	return m.saveAsset(owner, base64.NewDecoder(base64.StdEncoding, strings.NewReader(encoded)), true, allowSVG)
}
