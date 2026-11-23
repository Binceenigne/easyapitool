package main

import (
	"bufio"
	"compress/gzip"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"strings"
)

// Shrink long JSON strings before inspecting metadata. Reference images and
// prompts stay intact in the private spool file forwarded to the upstream.
func policyJSON(reader io.Reader) (object, error) {
	in := bufio.NewReader(reader)
	out := make([]byte, 0, 4096)
	for {
		c, err := in.ReadByte()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		if c == '"' {
			value := []byte{'"'}
			escaped, large := false, false
			for {
				x, err := in.ReadByte()
				if err != nil {
					return nil, errors.New("JSON 字符串不完整")
				}
				if !large {
					value = append(value, x)
					if len(value) > 2048 {
						large = true
						value = nil
					}
				}
				if x == '"' && !escaped {
					break
				}
				if x == '\\' && !escaped {
					escaped = true
				} else {
					escaped = false
				}
			}
			if large {
				out = append(out, '"', '"')
			} else {
				out = append(out, value...)
			}
		} else if c != ' ' && c != '\t' && c != '\r' && c != '\n' {
			out = append(out, c)
		}
		if len(out) > 2<<20 {
			return nil, errors.New("请求的结构或元数据过大")
		}
	}
	decoder := json.NewDecoder(strings.NewReader(string(out)))
	var value func(int) (any, error)
	value = func(depth int) (any, error) {
		if depth > 128 {
			return nil, errors.New("JSON 嵌套过深")
		}
		token, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		if delimiter, ok := token.(json.Delim); ok {
			switch delimiter {
			case '{':
				m := object{}
				for decoder.More() {
					key, err := decoder.Token()
					if err != nil {
						return nil, err
					}
					name, ok := key.(string)
					if !ok {
						return nil, errors.New("JSON 字段无效")
					}
					if _, exists := m[name]; exists {
						return nil, errors.New("JSON 含有重复字段")
					}
					m[name], err = value(depth + 1)
					if err != nil {
						return nil, err
					}
				}
				_, err = decoder.Token()
				return m, err
			case '[':
				a := []any{}
				for decoder.More() {
					v, err := value(depth + 1)
					if err != nil {
						return nil, err
					}
					a = append(a, v)
				}
				_, err = decoder.Token()
				return a, err
			default:
				return nil, errors.New("JSON 结构无效")
			}
		}
		return token, nil
	}
	v, err := value(0)
	if err != nil {
		return nil, err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, errors.New("JSON 请求含有多余数据")
	}
	result, ok := v.(map[string]any)
	if !ok {
		return nil, errors.New("请使用 JSON 对象")
	}
	return result, nil
}

func inputImageCount(v any) int {
	switch x := v.(type) {
	case map[string]any:
		n := 0
		if x["type"] == "input_image" {
			n++
		}
		for _, value := range x {
			n += inputImageCount(value)
		}
		return n
	case []any:
		n := 0
		for _, value := range x {
			n += inputImageCount(value)
		}
		return n
	}
	return 0
}

func (g *gateway) authorizeProxyBody(r *http.Request, k accessKey, path string, body io.Reader) (io.Reader, func(), error) {
	file, err := os.CreateTemp(g.cfg.assetDir, "mirra-request-*")
	if err != nil {
		return nil, nil, err
	}
	cleanup := func() { file.Close(); os.Remove(file.Name()) }
	fail := func(err error) (io.Reader, func(), error) { cleanup(); return nil, nil, err }
	if _, err := io.Copy(file, body); err != nil {
		return fail(err)
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return fail(err)
	}
	var read io.Reader = file
	if encoding := r.Header.Get("Content-Encoding"); encoding != "" {
		if encoding != "gzip" {
			return fail(errors.New("档位校验支持 JSON、表单或 gzip 请求"))
		}
		gz, err := gzip.NewReader(file)
		if err != nil {
			return fail(errors.New("gzip 请求无效"))
		}
		defer gz.Close()
		read = io.LimitReader(gz, maxProxyBody+1)
	}
	kind, parameters, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	var data object
	refs := 0
	if kind == "multipart/form-data" {
		data = object{}
		form := multipart.NewReader(read, parameters["boundary"])
		for {
			part, err := form.NextPart()
			if err == io.EOF {
				break
			}
			if err != nil {
				return fail(errors.New("图片表单不完整"))
			}
			name := part.FormName()
			if part.FileName() != "" {
				if name == "image" || strings.HasPrefix(name, "image[") {
					refs++
				}
				_, err = io.Copy(io.Discard, part)
			} else {
				if _, exists := data[name]; exists {
					return fail(errors.New("图片表单含有重复参数"))
				}
				value, readErr := io.ReadAll(io.LimitReader(part, 32769))
				err = readErr
				if len(value) > 32768 && name != "prompt" {
					return fail(errors.New("图片表单字段过长"))
				}
				data[name] = string(value)
			}
			part.Close()
			if err != nil {
				return fail(err)
			}
		}
	} else if kind == "application/json" || kind == "" {
		data, err = policyJSON(read)
		if err != nil {
			return fail(err)
		}
		if images, ok := data["images"].([]any); ok {
			refs = len(images)
		}
		if data["image"] != nil {
			refs = max(refs, 1)
		}
	} else {
		return fail(errors.New("请使用 JSON 或图片表单请求"))
	}
	if strings.HasPrefix(path, "/images/") {
		if err := g.checkNativeImage(k, data, refs, path != "/images/generations"); err != nil {
			return fail(err)
		}
	} else if path == "/responses" {
		profile, profileErr := g.modelsForKey(k)
		if profileErr != nil {
			return fail(profileErr)
		}
		refs := inputImageCount(data["input"])
		if refs > 0 && (!profile.Tier.ImageEditing || refs > profile.Tier.MaxReferences) {
			return fail(denied("此参考图数量或图片分析"))
		}
		model := objectString(data, "model")
		effort := effectiveReasoningEffort(model, objectString(asObject(data["reasoning"]), "effort"))
		web := false
		if tools, ok := data["tools"].([]any); ok {
			for _, item := range tools {
				tool := asObject(item)
				kind := objectString(tool, "type")
				name := objectString(tool, "name")
				if name == "" {
					name = objectString(asObject(tool["function"]), "name")
				}
				if strings.HasPrefix(kind, "web_search") || oneOf(name, "search_web", "search_visual_references", "select_visual_references", "web_search") {
					web = true
				}
				if kind == "image_generation" {
					if err := g.checkNativeImage(k, tool, inputImageCount(data["input"]), false); err != nil {
						return fail(err)
					}
					profile, _ := g.modelsForKey(k)
					if profile != nil && profile.Tier.MaxImages < 9 {
						n, ok := tokenCount(data["max_tool_calls"])
						if !ok || n < 1 || n > int64(profile.Tier.MaxImages) {
							return fail(denied("此图像工具调用上限，请明确设置 max_tool_calls"))
						}
					}
				}
			}
		}
		if err := g.checkReasoningTier(k, model, effort, r.Header.Get("X-Mirra-Reasoning-Mode"), web); err != nil {
			return fail(err)
		}
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return fail(err)
	}
	return file, cleanup, nil
}

func (g *gateway) checkNativeImage(k accessKey, data object, refs int, edit bool) error {
	profile, err := g.modelsForKey(k)
	if err != nil {
		return err
	}
	model := objectString(data, "model")
	if model == "" {
		model = profile.DefaultImage
	}
	quality := objectString(data, "quality")
	if quality == "" {
		quality = "auto"
	}
	format := objectString(data, "output_format")
	if format == "" {
		format = "png"
	}
	background := objectString(data, "background")
	if background == "" {
		background = "auto"
	}
	size := objectString(data, "size")
	if size == "" {
		size = "auto"
	}
	n := 1
	if value, exists := data["n"]; exists {
		if text, ok := value.(string); ok {
			var parsed int
			if _, err := fmt.Sscan(text, &parsed); err != nil {
				return errors.New("图片数量无效")
			}
			n = parsed
		} else {
			parsed, ok := tokenCount(value)
			if !ok {
				return errors.New("图片数量无效")
			}
			n = int(parsed)
		}
	}
	if edit {
		refs = max(refs, 1)
	}
	o := imageOptions{model: model, quality: quality, format: format, background: background, size: size, n: n, complexity: objectString(data, "complexity")}
	if n < 1 || !validImageSize(size) {
		return errors.New("图片数量或尺寸无效")
	}
	if format == "svg" && !profile.SVG.Enabled {
		return denied("SVG")
	}
	if frame, exists := data["frame_count"]; exists {
		v := 0
		if text, ok := frame.(string); ok {
			if _, err := fmt.Sscan(text, &v); err != nil {
				return errors.New("GIF 帧数无效")
			}
		} else {
			number, ok := tokenCount(frame)
			if !ok {
				return errors.New("GIF 帧数无效")
			}
			v = int(number)
		}
		o.frameCount = &v
	}
	return g.checkImageTier(k, o, refs)
}
