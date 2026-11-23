package main

import (
	"context"
	"crypto/tls"
	"database/sql"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type trackedImageUpload struct {
	io.Reader
	closed atomic.Int32
}

func (b *trackedImageUpload) Read(p []byte) (int, error) {
	if b.closed.Load() != 0 {
		return 0, errors.New("upload was already closed")
	}
	return b.Reader.Read(p)
}

func (b *trackedImageUpload) Close() error { b.closed.Add(1); return nil }

func TestImageConnectionRetryNeverReplaysSubmittedPost(t *testing.T) {
	for _, tc := range []struct {
		name                              string
		failedTunnels, reject             int
		disconnect, invalidCert, canceled bool
		wantTunnels, wantPosts            int32
		wantError, wantUnsent             bool
	}{
		{name: "TLS fails before streamed generation upload", failedTunnels: 1, wantTunnels: 2, wantPosts: 1},
		{name: "both TLS handshakes fail", failedTunnels: 2, wantTunnels: 2, wantError: true, wantUnsent: true},
		{name: "connection closes after submission", disconnect: true, wantTunnels: 1, wantPosts: 1, wantError: true},
		{name: "proxy rejects credentials", reject: 407, wantTunnels: 1, wantError: true, wantUnsent: true},
		{name: "certificate verification fails", invalidCert: true, wantTunnels: 1, wantError: true, wantUnsent: true},
		{name: "cancellation during backoff", failedTunnels: 1, canceled: true, wantTunnels: 1, wantError: true, wantUnsent: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			const payload = `{"model":"image-fixture","prompt":"preserve this streaming upload"}`
			var tunnels, posts atomic.Int32
			upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				posts.Add(1)
				body, err := io.ReadAll(r.Body)
				if err != nil || string(body) != payload {
					t.Errorf("image upload changed: bytes=%d err=%v", len(body), err)
				}
				if tc.disconnect {
					connection, _, err := http.NewResponseController(w).Hijack()
					if err != nil {
						t.Error(err)
						return
					}
					connection.Close()
					return
				}
				w.Header().Set("Content-Type", "application/json")
				io.WriteString(w, `{"data":[]}`)
			}))
			defer upstream.Close()
			proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				n := tunnels.Add(1)
				if r.Method != http.MethodConnect || r.Host != "api.openai.com:443" {
					t.Errorf("unexpected CONNECT target: %s %s", r.Method, r.Host)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				if tc.reject != 0 {
					w.WriteHeader(tc.reject)
					return
				}
				tunnel, buffered, err := http.NewResponseController(w).Hijack()
				if err != nil {
					t.Error(err)
					return
				}
				defer tunnel.Close()
				io.WriteString(buffered, "HTTP/1.1 200 Connection Established\r\n\r\n")
				if err := buffered.Flush(); err != nil {
					t.Error(err)
					return
				}
				if n <= int32(tc.failedTunnels) {
					return
				}
				origin, err := net.DialTimeout("tcp", upstream.Listener.Addr().String(), time.Second)
				if err != nil {
					t.Error(err)
					return
				}
				defer origin.Close()
				go func() { io.Copy(origin, buffered); origin.Close() }()
				io.Copy(tunnel, origin)
			}))
			defer proxy.Close()
			transport, _, err := (&egressManager{}).prepare(proxySettings{Mode: "custom", Scope: "openai", URL: proxy.URL})
			if err != nil {
				t.Fatal(err)
			}
			defer transport.CloseIdleConnections()
			transport.images.TLSClientConfig = &tls.Config{ServerName: "127.0.0.1"}
			if !tc.invalidCert {
				transport.images.TLSClientConfig.RootCAs = upstream.Client().Transport.(*http.Transport).TLSClientConfig.RootCAs
			}
			ctx := t.Context()
			if tc.canceled {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, 120*time.Millisecond)
				defer cancel()
			}
			upload := &trackedImageUpload{Reader: strings.NewReader(payload)}
			req, err := http.NewRequestWithContext(ctx, http.MethodPost, "https://api.openai.com/v1/images/generations", upload)
			if err != nil {
				t.Fatal(err)
			}
			g := testGateway(t)
			setTestRPM(t, g, 1)
			account := addUpstream(t, g, "retry-rpm", "", openAIBaseURL)
			key, _ := issue(t, g, accessKey{Name: "retry", Upstream: account.ID})
			selected := route{key: key, upstream: account}
			reservation, err := g.reserve(ctx, selected)
			if err != nil {
				t.Fatal(err)
			}
			res, err := (&http.Client{Transport: transport, Timeout: 5 * time.Second}).Do(req)
			if res != nil {
				io.Copy(io.Discard, res.Body)
				res.Body.Close()
			}
			if (err != nil) != tc.wantError {
				t.Fatalf("unexpected result: %v", err)
			}
			if tunnels.Load() != tc.wantTunnels || posts.Load() != tc.wantPosts {
				t.Fatalf("tunnels=%d posts=%d, expected %d / %d", tunnels.Load(), posts.Load(), tc.wantTunnels, tc.wantPosts)
			}
			if (unsentImageUsage(err) != nil) != tc.wantUnsent {
				t.Fatalf("incorrect certainty that the request was never sent: %v", err)
			}
			if upload.closed.Load() != 1 {
				t.Fatalf("streaming body close count=%d", upload.closed.Load())
			}
			status := http.StatusBadGateway
			if res != nil {
				status = res.StatusCode
			}
			g.recordPriced(reservation, status, unsentImageUsage(err), "", false)
			var admissions int
			g.store.db.QueryRow("SELECT COUNT(*) FROM requests WHERE key_id=?", key.ID).Scan(&admissions)
			if admissions != 1 {
				t.Fatal("safe transport retry produced duplicate RPM or billing admissions")
			}
			if _, next := g.reserve(t.Context(), selected); rpmFailure(next) == nil {
				t.Fatal("one logical transport attempt did not consume exactly one RPM slot")
			}
		})
	}
}

func TestUnsentImageFailureReleasesUSDReservation(t *testing.T) {
	g := testGateway(t)
	u := addUpstream(t, g, "unsent", "", openAIBaseURL)
	k, _ := issue(t, g, accessKey{Name: "preflight", Upstream: u.ID})
	for _, unsent := range []bool{true, false} {
		id, err := g.store.reserve(t.Context(), route{key: k, upstream: u}, time.Now(), 1000)
		if err != nil {
			t.Fatal(err)
		}
		var count *meter
		if unsent {
			count = unsentImageUsage(&upstreamNotSentError{io.ErrUnexpectedEOF})
		}
		g.recordPriced(id, http.StatusBadGateway, count, "", false)
		var cost sql.NullInt64
		if err := g.store.db.QueryRow("SELECT cost_micro FROM requests WHERE id=?", id).Scan(&cost); err != nil {
			t.Fatal(err)
		}
		if cost.Valid != unsent || cost.Int64 != 0 {
			t.Fatalf("unsent=%v, settled cost=%+v", unsent, cost)
		}
	}
}
