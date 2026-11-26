// Package server builds the go-api HTTP handler and runs it with graceful shutdown.
package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/dmitrycham/llm-gateway/services/go-api/internal/logging"
	"github.com/dmitrycham/llm-gateway/services/go-api/internal/requestid"
)

const (
	// readHeaderTimeout guards against clients that never finish their headers
	// (Slowloris). There is no WriteTimeout on purpose: SSE responses stay open.
	readHeaderTimeout = 10 * time.Second
	// readTimeout bounds reading the whole request, body included. Without it a client
	// that stalls mid-body holds the connection forever: net/http reads up to 256 KB of
	// an unread body before it replies, even on /health/live. Large uploads should
	// extend their own deadline with http.ResponseController.SetReadDeadline.
	readTimeout = 15 * time.Second
	idleTimeout = 120 * time.Second
)

// NewRouter returns the router with the common middleware and all routes.
func NewRouter(logger *slog.Logger) *chi.Mux {
	r := chi.NewRouter()
	// Order matters: the request ID must exist before anything logs, and the access log
	// wraps the recoverer so that it records the 500 the recoverer writes.
	r.Use(requestid.Middleware, logging.AccessLog(logger), logging.Recoverer(logger))
	r.Get("/health/live", health)
	return r
}

// health is the liveness probe (spec §7.1): it answers while the process can serve HTTP.
// /health/ready comes with the first dependency (the database pool).
func health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = io.WriteString(w, `{"status":"ok"}`+"\n")
}

// New returns an http.Server for h whose internal errors go to logger.
func New(h http.Handler, logger *slog.Logger) *http.Server {
	return &http.Server{
		Handler:           h,
		ReadHeaderTimeout: readHeaderTimeout,
		ReadTimeout:       readTimeout,
		IdleTimeout:       idleTimeout,
		ErrorLog:          slog.NewLogLogger(logger.Handler(), slog.LevelWarn),
	}
}

// Run serves on ln until ctx is done, then shuts down gracefully: the listener closes at
// once, so new connections are refused, and in-flight requests get up to timeout to
// finish. After the timeout the remaining connections are closed and Run returns an
// error that wraps context.DeadlineExceeded.
//
// Run takes a listener instead of an address so that tests can serve on an in-memory
// listener and the caller can log the real address of ":0".
func Run(ctx context.Context, srv *http.Server, ln net.Listener, timeout time.Duration) error {
	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.Serve(ln) }()

	select {
	case err := <-serveErr:
		return fmt.Errorf("serve: %w", err)
	case <-ctx.Done():
	}

	// ctx is already done, so the shutdown deadline needs a context that ctx cannot cancel.
	shutdownCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), timeout)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		// Drop the requests that are still running so the process can exit.
		return errors.Join(fmt.Errorf("shutdown: %w", err), srv.Close())
	}
	// Shutdown makes Serve return ErrServerClosed at once; anything else is a real error.
	if err := <-serveErr; !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("serve: %w", err)
	}
	return nil
}
