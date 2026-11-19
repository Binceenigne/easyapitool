package main

import (
	"archive/zip"
	"bytes"
	"io"
	"io/fs"
	"net/http"
	"path/filepath"
)

const previewPrefix = "assets/public/"
const previewPolicy = "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'none'; " +
	"frame-ancestors 'self'; form-action 'none'; base-uri 'none'; sandbox allow-scripts"

var previewContentTypes = map[string]string{
	".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
	".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml",
	".woff2": "font/woff2",
}

// Serve the same frontend assets included in the signed Android release.
func (m *mobile) appPreview(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("asset")
	if name == "preview-bridge.js" {
		bridge, err := adminFiles.ReadFile("web/preview-bridge.js")
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", previewContentTypes[".js"])
		w.Write(bridge)
		return
	}
	entry := name == "" || name == "index.html"
	contentType := previewContentTypes[filepath.Ext(name)]
	if entry {
		name, contentType = "index.html", "text/html; charset=utf-8"
	}
	if contentType == "" || !fs.ValidPath(name) {
		http.NotFound(w, r)
		return
	}
	root := m.g.cfg.androidReleases
	if root == "" {
		http.Error(w, "请配置 Android 安装包发布目录", http.StatusServiceUnavailable)
		return
	}
	manifest, file, err := m.verifyRelease(root, "android", ".apk")
	if err != nil {
		http.Error(w, "Android 安装包不可用", http.StatusServiceUnavailable)
		return
	}
	defer file.Close()
	archive, err := zip.NewReader(file, manifest.SizeBytes)
	if err != nil {
		http.Error(w, "无法读取 Android 安装包", http.StatusServiceUnavailable)
		return
	}
	if entry {
		page, err := fs.ReadFile(archive, previewPrefix+name)
		if err != nil {
			http.Error(w, "安装包缺少预览页面", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Security-Policy", previewPolicy)
		w.Header().Set("Content-Type", contentType)
		w.Write(bytes.Replace(page, []byte("<head>"), []byte(`<head><script src="preview-bridge.js"></script>`), 1))
		return
	}
	asset, err := archive.Open(previewPrefix + name)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer asset.Close()
	w.Header().Set("Content-Type", contentType)
	io.Copy(w, asset)
}
