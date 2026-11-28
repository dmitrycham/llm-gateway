// Package health serves the gateway probes (gateway.yaml, tag health).
package health

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"time"
)

// Check reports whether a dependency is reachable. It must return by ctx's deadline.
type Check func(ctx context.Context) bool

// DialFunc opens a connection; net.Dialer.DialContext in production.
type DialFunc func(ctx context.Context, network, addr string) (net.Conn, error)

type liveness struct {
	Status string `json:"status"`
}

type readiness struct {
	Status   string            `json:"status"`
	Degraded bool              `json:"degraded"`
	Checks   map[string]string `json:"checks"`
}

// Handler returns the probe routes of the internal listener (:8081). postgres decides
// readiness; timeout bounds the whole check so a stalled one cannot hold the probe.
func Handler(postgres Check, timeout time.Duration) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /internal/health/live", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, liveness{Status: "ok"})
	})
	mux.HandleFunc("GET /internal/health/ready", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), timeout)
		defer cancel()
		// No provider checks yet: providers arrive in Stage 2, so degraded stays false.
		if postgres(ctx) {
			writeJSON(w, http.StatusOK, readiness{
				Status: "ready", Checks: map[string]string{"postgres": "ok"},
			})
			return
		}
		writeJSON(w, http.StatusServiceUnavailable, readiness{
			Status: "not_ready", Checks: map[string]string{"postgres": "fail"},
		})
	})
	return mux
}

// TCPCheck passes when a TCP connection to addr opens.
//
// Placeholder until the gateway has its PostgreSQL pool; that ticket replaces it with
// a pool ping, as in ai-service.
func TCPCheck(addr string, dial DialFunc) Check {
	return func(ctx context.Context) bool {
		conn, err := dial(ctx, "tcp", addr)
		if err != nil {
			return false
		}
		_ = conn.Close()
		return true
	}
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
