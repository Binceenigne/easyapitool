package main

import (
	"encoding/xml"
	"errors"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	"image/png"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"

	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
)

const maxImageBytes = 50 << 20

type asset struct {
	id, owner, path, format, contentType string
	size                                 int64
	width, height, holds                 int
	expires                              time.Time
}

var imageMIME = map[string]string{
	"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif", "svg": "image/svg+xml",
}

func (m *mobile) saveAsset(owner string, reader io.Reader, output bool) (asset, error) {
	if !m.reserveAssetBytes(maxImageBytes) {
		return asset{}, errors.New("临时图片空间已满，请稍后重试")
	}
	defer m.releaseAssetBytes(maxImageBytes)
	file, err := os.CreateTemp(m.instance, "image-")
	if err != nil {
		return asset{}, errors.New("无法保存临时图片")
	}
	saved := false
	defer func() {
		file.Close()
		if !saved {
			os.Remove(file.Name())
		}
	}()
	size, err := io.Copy(file, io.LimitReader(reader, maxImageBytes+1))
	if err != nil || size == 0 || size > maxImageBytes {
		return asset{}, errors.New("图片内容无效或超过 50 MB")
	}
	file.Seek(0, io.SeekStart)
	dimensions, format, err := image.DecodeConfig(file)
	if err != nil && output && m.g.cfg.svgEnabled && isSVG(file) {
		format, err = "svg", nil
	}
	if err != nil || imageMIME[format] == "" || (!output && !oneOf(format, "png", "jpeg", "webp")) {
		return asset{}, errors.New("图片格式不受支持或内容无效")
	}
	a := asset{id: randomID(18), owner: owner, path: file.Name(), size: size, format: format,
		contentType: imageMIME[format], width: dimensions.Width, height: dimensions.Height, expires: time.Now().Add(resourceTTL)}
	m.mu.Lock()
	m.assets[a.id] = a
	m.assetBytes += size
	m.mu.Unlock()
	saved = true
	return a, nil
}

func (m *mobile) reserveAssetBytes(n int64) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.assetBytes+n > 2<<30 {
		return false
	}
	m.assetBytes += n
	return true
}

func (m *mobile) releaseAssetBytes(n int64) {
	m.mu.Lock()
	m.assetBytes -= n
	m.mu.Unlock()
}

func isSVG(file *os.File) bool {
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return false
	}
	parser := xml.NewDecoder(io.LimitReader(file, maxImageBytes))
	root := false
	for {
		token, err := parser.Token()
		if err != nil {
			return root && err == io.EOF
		}
		switch item := token.(type) {
		case xml.Directive:
			return false
		case xml.StartElement:
			if !root {
				if item.Name.Local != "svg" || !oneOf(item.Name.Space, "", "http://www.w3.org/2000/svg") {
					return false
				}
				root = true
			}
			if oneOf(strings.ToLower(item.Name.Local), "script", "foreignobject") {
				return false
			}
			for _, attr := range item.Attr {
				name := strings.ToLower(attr.Name.Local)
				value := strings.TrimSpace(attr.Value)
				if strings.HasPrefix(name, "on") || (name == "href" && !strings.HasPrefix(value, "#")) {
					return false
				}
			}
		}
	}
}

func (m *mobile) preview(original asset) asset {
	if max(original.width, original.height) <= 640 || int64(original.width)*int64(original.height) > 9_000_000 || original.format == "gif" {
		return original
	}
	m.thumbnailMu.Lock()
	defer m.thumbnailMu.Unlock()
	file, err := os.Open(original.path)
	if err != nil {
		return original
	}
	defer file.Close()
	source, _, err := image.Decode(file)
	if err != nil {
		return original
	}
	long := max(original.width, original.height)
	width, height := max(1, original.width*640/long), max(1, original.height*640/long)
	thumbnail := image.NewNRGBA(image.Rect(0, 0, width, height))
	draw.ApproxBiLinear.Scale(thumbnail, thumbnail.Bounds(), source, source.Bounds(), draw.Src, nil)
	reader, writer := io.Pipe()
	defer reader.Close()
	go func() { writer.CloseWithError(png.Encode(writer, thumbnail)) }()
	preview, err := m.saveAsset(original.owner, reader, true)
	if err != nil {
		return original
	}
	return preview
}

func assetInfo(original, preview asset) object {
	return object{"resourceId": original.id, "downloadUrl": "/api/v1/assets/" + original.id,
		"previewResourceId": preview.id, "previewUrl": "/api/v1/assets/" + preview.id, "previewMimeType": preview.contentType,
		"width": original.width, "height": original.height, "sizeBytes": original.size, "contentType": original.contentType,
		"mimeType": original.contentType, "format": original.format}
}

func (m *mobile) uploadReference(w http.ResponseWriter, r *http.Request, owner string) {
	r.Body = http.MaxBytesReader(w, r.Body, maxImageBytes+(1<<20))
	parts, err := r.MultipartReader()
	if err != nil {
		jsonError(w, http.StatusBadRequest, "请使用文件上传")
		return
	}
	for {
		part, err := parts.NextPart()
		if err != nil {
			jsonError(w, http.StatusBadRequest, "参考图文件缺失或无效")
			return
		}
		if part.FormName() != "file" || part.FileName() == "" {
			part.Close()
			continue
		}
		a, err := m.saveAsset(owner, part, false)
		if err != nil {
			jsonError(w, http.StatusBadRequest, err.Error())
			return
		}
		info := assetInfo(a, m.preview(a))
		name := strings.TrimSuffix(part.FileName(), filepath.Ext(part.FileName()))
		name = strings.Map(func(r rune) rune {
			if unicode.IsLetter(r) || unicode.IsNumber(r) || strings.ContainsRune("-_ ", r) {
				return r
			}
			return -1
		}, name)
		if clean := []rune(strings.TrimSpace(name)); len(clean) > 48 {
			name = string(clean[:48])
		}
		if strings.TrimSpace(name) == "" {
			name = "reference"
		}
		info["name"] = name + extension(a.format)
		writeJSON(w, http.StatusOK, object{"ok": true, "resource": info})
		return
	}
}

func extension(format string) string {
	if format == "jpeg" {
		return ".jpg"
	}
	return "." + format
}

func (m *mobile) downloadAsset(w http.ResponseWriter, r *http.Request, owner string) {
	m.mu.Lock()
	a, ok := m.assets[r.PathValue("id")]
	m.mu.Unlock()
	if !ok || a.owner != owner || time.Now().After(a.expires) {
		jsonError(w, http.StatusNotFound, "资源不存在或已过期")
		return
	}
	file, err := os.Open(a.path)
	if err != nil {
		jsonError(w, http.StatusNotFound, "资源不存在")
		return
	}
	defer file.Close()
	w.Header().Set("Content-Type", a.contentType)
	w.Header().Set("Cache-Control", "private, no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if a.format == "svg" {
		w.Header().Set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox")
		w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": a.id + ".svg"}))
	}
	http.ServeContent(w, r, a.id+extension(a.format), time.Time{}, file)
}
