package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	appName     = "DJYX_APITOOL"
	deviceTTL   = 365 * 24 * time.Hour
	resourceTTL = 30 * time.Minute
)

type mobile struct {
	ctx         context.Context
	g           *gateway
	instance    string
	epoch       string
	mu          sync.Mutex
	pairMu      sync.Mutex
	thumbnailMu sync.Mutex
	releaseMu   sync.Mutex
	releases    map[string]releaseVerification
	workers     sync.WaitGroup
	keys        map[credentialID]*mobileKey
	assets      map[string]asset
	assetBytes  int64
	tasks       map[credentialID]*task
	events      []event
	sequence    int64
	changed     chan struct{}
	capacity    chan struct{}
}

func newMobile(ctx context.Context, g *gateway, instance string) *mobile {
	m := &mobile{ctx: ctx, g: g, instance: instance, epoch: randomID(18),
		keys: make(map[credentialID]*mobileKey), assets: make(map[string]asset), tasks: make(map[credentialID]*task),
		changed: make(chan struct{}), capacity: make(chan struct{}, 2), releases: make(map[string]releaseVerification)}
	m.workers.Add(1)
	go m.cleanupLoop()
	return m
}

func (m *mobile) registerRoutes(root *http.ServeMux) {
	root.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, object{"ok": true, "service": appName, "status": "alive"})
	})
	root.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		err := m.g.store.db.PingContext(ctx)
		_, diskErr := os.Stat(m.instance)
		payload := object{"ok": err == nil && diskErr == nil, "status": "ready", "persistentServerDatabase": true,
			"checks": object{"database": err == nil, "workRoot": diskErr == nil, "credentialsInMemory": true}}
		if err != nil || diskErr != nil {
			payload["status"] = "not_ready"
			writeJSON(w, http.StatusServiceUnavailable, payload)
			return
		}
		writeJSON(w, http.StatusOK, payload)
	})
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/v1/pair", m.pair)
	mux.HandleFunc("POST /api/v1/device/register", m.registerDevice)
	mux.HandleFunc("GET /api/v1/app/update", m.appUpdate(false))
	mux.HandleFunc("GET /api/v1/app/update/download", m.appUpdate(true))
	mux.HandleFunc("GET /api/v1/state", m.auth(m.getState))
	mux.HandleFunc("POST /api/v1/refresh", m.auth(m.refresh))
	mux.HandleFunc("POST /api/v1/keys", m.auth(m.addKey))
	mux.HandleFunc("DELETE /api/v1/keys/{id}", m.auth(m.deleteKey))
	mux.HandleFunc("POST /api/v1/prompts/polish", m.auth(m.polish))
	mux.HandleFunc("POST /api/v1/uploads/references", m.auth(m.uploadReference))
	mux.HandleFunc("GET /api/v1/assets/{id}", m.auth(m.downloadAsset))
	mux.HandleFunc("GET /api/v1/image-sessions", m.auth(func(w http.ResponseWriter, r *http.Request, owner string) {
		writeJSON(w, http.StatusOK, object{"ok": true, "sets": []object{}, "source": "android-local"})
	}))
	mux.HandleFunc("POST /api/v1/image-generations", m.auth(m.createGeneration))
	mux.HandleFunc("GET /api/v1/image-generations/{id}", m.auth(m.taskState))
	mux.HandleFunc("POST /api/v1/image-generations/{id}/cancel", m.auth(m.cancelTask))
	mux.HandleFunc("GET /api/v1/events", m.auth(m.streamEvents))
	root.Handle("/api/v1/", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if oneOf(origin, "capacitor://localhost", "http://localhost", "https://localhost") {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Accept, Authorization, Content-Type")
			w.Header().Set("Access-Control-Expose-Headers", "X-Event-Epoch")
			w.Header().Add("Vary", "Origin")
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}
		}
		mux.ServeHTTP(w, r)
	}))
}

type deviceToken struct {
	DeviceID  string `json:"deviceId"`
	ExpiresAt int64  `json:"expiresAt"`
}

func (m *mobile) signature(payload string) []byte {
	hash := hmac.New(sha256.New, []byte(m.g.cfg.token))
	hash.Write([]byte(payload))
	return hash.Sum(nil)
}

func (m *mobile) issueDeviceToken(deviceID string, now time.Time) string {
	encoded, _ := json.Marshal(deviceToken{DeviceID: deviceID, ExpiresAt: now.Add(deviceTTL).Unix()})
	payload := base64.RawURLEncoding.EncodeToString(encoded)
	return "device-v1." + payload + "." + base64.RawURLEncoding.EncodeToString(m.signature(payload))
}

func (m *mobile) owner(token string, now time.Time) string {
	if hmac.Equal([]byte(token), []byte(m.g.cfg.token)) {
		return "paired-device"
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 || parts[0] != "device-v1" {
		return ""
	}
	signature, err := base64.RawURLEncoding.Strict().DecodeString(parts[2])
	if err != nil || !hmac.Equal(m.signature(parts[1]), signature) {
		return ""
	}
	decoded, err := base64.RawURLEncoding.Strict().DecodeString(parts[1])
	var claims deviceToken
	if err != nil || json.Unmarshal(decoded, &claims) != nil || !validDevice(claims.DeviceID) || claims.ExpiresAt <= now.Unix() {
		return ""
	}
	return "device:" + claims.DeviceID
}

func validDevice(id string) bool { return len(id) >= 16 && len(id) <= 128 }

func (m *mobile) auth(handler func(http.ResponseWriter, *http.Request, string)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		owner := m.owner(bearer(r), time.Now())
		if owner == "" {
			jsonError(w, http.StatusUnauthorized, "未授权")
			return
		}
		handler(w, r, owner)
	}
}

func (m *mobile) pair(w http.ResponseWriter, r *http.Request) {
	var req struct{ PairingCode string }
	if !decodeRequest(w, r, 4096, &req) {
		return
	}
	code := strings.TrimSpace(req.PairingCode)
	path := filepath.Join(m.g.cfg.workDir, "pairing-code")
	m.pairMu.Lock()
	defer m.pairMu.Unlock()
	if validDevice(code) {
		if stat, err := os.Lstat(path); err == nil && stat.Mode().IsRegular() && stat.Mode().Perm()&0077 == 0 && stat.Size() <= 512 {
			if expected, err := os.ReadFile(path); err == nil && hmac.Equal([]byte(code), []byte(strings.TrimSpace(string(expected)))) {
				if os.Remove(path) == nil {
					writeJSON(w, http.StatusOK, object{"ok": true, "bearerToken": m.g.cfg.token})
					return
				}
			}
		}
	}
	jsonError(w, http.StatusUnauthorized, "配对码无效")
}

func (m *mobile) cleanupLoop() {
	defer m.workers.Done()
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-m.ctx.Done():
			return
		case now := <-ticker.C:
			m.mu.Lock()
			for id, a := range m.assets {
				if a.holds == 0 && now.After(a.expires) {
					delete(m.assets, id)
					m.assetBytes -= a.size
					os.Remove(a.path)
				}
			}
			for id, job := range m.tasks {
				if job.cancel == nil && now.After(job.lastChanged.Add(resourceTTL)) {
					delete(m.tasks, id)
				}
			}
			for id, key := range m.keys {
				if now.After(key.updated.Add(24 * time.Hour)) {
					delete(m.keys, id)
				}
			}
			m.mu.Unlock()
		}
	}
}
