// Command gateway is the AI Gateway service.
//
// Until Stage 2 it serves only the health probes on the internal listener (:8081,
// gateway.yaml): ai-service readiness needs the gateway, so Compose cannot report a
// healthy stack without it. Stage 2 adds the public listener (:8080), config, request
// IDs and logging middleware, as in go-api.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/dmitrycham/llm-gateway/services/gateway/internal/health"
)

const (
	defaultInternalAddr = ":8081"
	defaultPostgresAddr = "postgres:5432"
	readinessTimeout    = time.Second
	shutdownTimeout     = 5 * time.Second
)

// listenFunc opens the listener; tests pass one that needs no port.
type listenFunc func(ctx context.Context, addr string) (net.Listener, error)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(os.Getenv, logger, listenTCP); err != nil {
		logger.Error("gateway stopped", "error", err)
		os.Exit(1)
	}
}

func listenTCP(ctx context.Context, addr string) (net.Listener, error) {
	return new(net.ListenConfig).Listen(ctx, "tcp", addr) //nolint:wrapcheck // run wraps it
}

func envOr(getenv func(string) string, key, fallback string) string {
	if v := getenv(key); v != "" {
		return v
	}
	return fallback
}

// run serves the probes until SIGTERM or SIGINT, then shuts down gracefully.
func run(getenv func(string) string, logger *slog.Logger, listen listenFunc) error {
	addr := envOr(getenv, "GATEWAY_INTERNAL_ADDR", defaultInternalAddr)
	postgresAddr := envOr(getenv, "GATEWAY_POSTGRES_ADDR", defaultPostgresAddr)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	ln, err := listen(ctx, addr)
	if err != nil {
		return fmt.Errorf("listen: %w", err)
	}
	logger.Info("listening", "addr", ln.Addr().String())

	var dialer net.Dialer
	srv := &http.Server{
		Handler:           health.Handler(health.TCPCheck(postgresAddr, dialer.DialContext), readinessTimeout),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		IdleTimeout:       120 * time.Second,
		ErrorLog:          slog.NewLogLogger(logger.Handler(), slog.LevelWarn),
	}
	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.Serve(ln) }()

	select {
	case err := <-serveErr:
		return fmt.Errorf("serve: %w", err)
	case <-ctx.Done():
	}
	// Restore the default handling, so a second signal kills the process at once.
	stop()
	logger.Info("shutting down")

	shutdownCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), shutdownTimeout)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		return errors.Join(fmt.Errorf("shutdown: %w", err), srv.Close())
	}
	if err := <-serveErr; !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("serve: %w", err)
	}
	logger.Info("stopped")
	return nil
}
