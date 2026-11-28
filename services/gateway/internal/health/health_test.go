package health

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func get(t *testing.T, h http.Handler, path string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequestWithContext(t.Context(), http.MethodGet, path, nil))
	return rec
}

func TestLive(t *testing.T) {
	h := Handler(func(context.Context) bool {
		t.Error("liveness must not run dependency checks")
		return false
	}, time.Second)

	rec := get(t, h, "/internal/health/live")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if got, want := rec.Body.String(), `{"status":"ok"}`+"\n"; got != want {
		t.Errorf("body = %q, want %q", got, want)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}
}

func TestReady(t *testing.T) {
	cases := []struct {
		name     string
		postgres bool
		code     int
		body     string
	}{
		{"postgres up", true, http.StatusOK,
			`{"status":"ready","degraded":false,"checks":{"postgres":"ok"}}` + "\n"},
		{"postgres down", false, http.StatusServiceUnavailable,
			`{"status":"not_ready","degraded":false,"checks":{"postgres":"fail"}}` + "\n"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := Handler(func(context.Context) bool { return tc.postgres }, time.Second)
			rec := get(t, h, "/internal/health/ready")
			if rec.Code != tc.code {
				t.Errorf("status = %d, want %d", rec.Code, tc.code)
			}
			if got := rec.Body.String(); got != tc.body {
				t.Errorf("body = %q, want %q", got, tc.body)
			}
		})
	}
}

func TestReadyCheckHasDeadline(t *testing.T) {
	h := Handler(func(ctx context.Context) bool {
		deadline, ok := ctx.Deadline()
		if !ok || time.Until(deadline) > 50*time.Millisecond {
			t.Errorf("check context deadline = %v (set: %v), want within 50ms", deadline, ok)
		}
		return true
	}, 50*time.Millisecond)
	get(t, h, "/internal/health/ready")
}

func TestUnknownPathAndMethod(t *testing.T) {
	h := Handler(func(context.Context) bool { return true }, time.Second)
	if rec := get(t, h, "/v1/models"); rec.Code != http.StatusNotFound {
		t.Errorf("GET /v1/models status = %d, want 404", rec.Code)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequestWithContext(t.Context(), http.MethodPost, "/internal/health/live", nil))
	if rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("POST live status = %d, want 405", rec.Code)
	}
}

func TestTCPCheck(t *testing.T) {
	t.Run("connects and closes", func(t *testing.T) {
		client, server := net.Pipe()
		t.Cleanup(func() { _ = server.Close() })
		var gotNetwork, gotAddr string
		check := TCPCheck("postgres:5432", func(_ context.Context, network, addr string) (net.Conn, error) {
			gotNetwork, gotAddr = network, addr
			return client, nil
		})
		if !check(t.Context()) {
			t.Fatal("check = false, want true")
		}
		if gotNetwork != "tcp" || gotAddr != "postgres:5432" {
			t.Errorf("dialed %s %s, want tcp postgres:5432", gotNetwork, gotAddr)
		}
		// The check must not leak the connection: a write to a closed pipe fails.
		if _, err := client.Write([]byte("x")); !errors.Is(err, io.ErrClosedPipe) {
			t.Errorf("write after check = %v, want io.ErrClosedPipe", err)
		}
	})
	t.Run("dial error", func(t *testing.T) {
		check := TCPCheck("postgres:5432", func(context.Context, string, string) (net.Conn, error) {
			return nil, errors.New("connection refused")
		})
		if check(t.Context()) {
			t.Error("check = true, want false")
		}
	})
}
