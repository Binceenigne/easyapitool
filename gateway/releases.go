package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type releaseManifest struct {
	Platform         string
	PackageName      string
	VersionCode      int64
	VersionName      string
	BuildTimestampMs int64
	ReleaseNotes     string
	FileName         string
	SizeBytes        int64
	SHA256           string
	MimeType         string
}

type releaseVerification struct {
	stat   os.FileInfo
	digest string
}

func (m *mobile) appUpdate(download bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		platform := r.URL.Query().Get("platform")
		if platform == "" {
			platform = "android"
		}
		var root, suffix, mimeType string
		switch platform {
		case "android":
			root, suffix, mimeType = m.g.cfg.androidReleases, ".apk", "application/vnd.android.package-archive"
		case "windows":
			root, suffix, mimeType = m.g.cfg.windowsReleases, ".exe", "application/octet-stream"
		}
		if root == "" {
			jsonError(w, http.StatusNotFound, "没有可用的发布通道")
			return
		}
		manifest, file, err := m.verifyRelease(root, platform, suffix)
		if err != nil {
			jsonError(w, http.StatusServiceUnavailable, err.Error())
			return
		}
		defer file.Close()
		if download {
			if manifest.MimeType != "" {
				mimeType = manifest.MimeType
			}
			w.Header().Set("Cache-Control", "no-cache")
			w.Header().Set("X-Content-Type-Options", "nosniff")
			w.Header().Set("Content-Type", mimeType)
			w.Header().Set("ETag", `"`+manifest.SHA256+`"`)
			w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": manifest.FileName}))
			http.ServeContent(w, r, manifest.FileName, time.UnixMilli(manifest.BuildTimestampMs), file)
			return
		}
		code, errCode := queryVersion(r, "currentVersionCode")
		stamp, errStamp := queryVersion(r, "currentBuildTimestampMs")
		if errCode || errStamp {
			jsonError(w, http.StatusBadRequest, "客户端版本字段无效")
			return
		}
		version := manifest.VersionName
		if version == "" {
			version = strconv.FormatInt(manifest.VersionCode, 10)
		}
		response := object{"ok": true, "platform": platform, "available": manifest.VersionCode > code || (manifest.VersionCode == code && manifest.BuildTimestampMs > stamp),
			"currentVersionCode": code, "currentBuildTimestampMs": stamp, "latestVersion": version, "latestVersionCode": manifest.VersionCode,
			"latestBuildTimestampMs": manifest.BuildTimestampMs, "latestBuildTime": time.UnixMilli(manifest.BuildTimestampMs).UTC().Format(time.RFC3339Nano),
			"releaseNotes": manifest.ReleaseNotes, "fileName": manifest.FileName, "downloadSize": manifest.SizeBytes, "sha256": manifest.SHA256,
			"downloadUrl": "/app/update/download?platform=" + platform}
		if platform == "android" {
			response["packageName"] = manifest.PackageName
		}
		writeJSON(w, http.StatusOK, response)
	}
}

func queryVersion(r *http.Request, field string) (int64, bool) {
	value := r.URL.Query().Get(field)
	if value == "" {
		return 0, false
	}
	number, err := strconv.ParseInt(value, 10, 64)
	return number, err != nil || number < 0
}

func (m *mobile) verifyRelease(root, platform, suffix string) (releaseManifest, *os.File, error) {
	var manifest releaseManifest
	root, err := filepath.EvalSymlinks(root)
	if err != nil {
		return manifest, nil, errors.New("发布清单不可用")
	}
	config, err := os.Open(filepath.Join(root, "manifest.json"))
	if err != nil {
		return manifest, nil, errors.New("发布清单不可用")
	}
	defer config.Close()
	err = json.NewDecoder(io.LimitReader(config, 64<<10)).Decode(&manifest)
	if err != nil || manifest.Platform != platform || manifest.VersionCode <= 0 || manifest.BuildTimestampMs <= 0 ||
		(platform == "android" && manifest.PackageName != "cn.easyapitool.mobile") ||
		manifest.FileName != filepath.Base(manifest.FileName) || !strings.HasSuffix(strings.ToLower(manifest.FileName), suffix) {
		return manifest, nil, errors.New("发布清单无效")
	}
	manifest.SHA256 = strings.ToLower(manifest.SHA256)
	if hash, err := hex.DecodeString(manifest.SHA256); err != nil || len(hash) != sha256.Size {
		return manifest, nil, errors.New("发布校验值无效")
	}
	path, err := filepath.EvalSymlinks(filepath.Join(root, manifest.FileName))
	if err != nil || filepath.Dir(path) != filepath.Clean(root) {
		return manifest, nil, errors.New("安装包不存在")
	}
	file, err := os.Open(path)
	if err != nil {
		return manifest, nil, errors.New("安装包不可用")
	}
	stat, err := file.Stat()
	if err != nil || !stat.Mode().IsRegular() || stat.Size() <= 0 || stat.Size() != manifest.SizeBytes {
		file.Close()
		return manifest, nil, errors.New("安装包大小校验失败")
	}
	m.releaseMu.Lock()
	defer m.releaseMu.Unlock()
	verified, ok := m.releases[platform]
	if !ok || !os.SameFile(verified.stat, stat) || verified.stat.ModTime() != stat.ModTime() || verified.stat.Size() != stat.Size() {
		hash := sha256.New()
		if _, err := io.Copy(hash, file); err != nil {
			file.Close()
			return manifest, nil, errors.New("安装包读取失败")
		}
		verified = releaseVerification{stat: stat, digest: hex.EncodeToString(hash.Sum(nil))}
		m.releases[platform] = verified
	}
	if verified.digest != manifest.SHA256 {
		file.Close()
		return manifest, nil, errors.New("安装包 SHA-256 校验失败")
	}
	return manifest, file, nil
}
