// Command go-api is the public API service.
package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"syscall"

	"github.com/dmitrycham/llm-gateway/services/go-api/internal/config"
	"github.com/dmitrycham/llm-gateway/services/go-api/internal/logging"
	"github.com/dmitrycham/llm-gateway/services/go-api/internal/server"
)

// listenFunc opens the listener; tests pass one that needs no port.
type listenFunc func(ctx context.Context, addr string) (net.Listener, error)

func main() {
	// JSON from the first line on: config errors are logged before cfg.LogLevel is known.
	slog.SetDefault(logging.New(os.Stdout, slog.LevelInfo))
	if err := run(os.Getenv, os.Stdout, listenTCP); err != nil {
		slog.Error("go-api stopped", "error", err)
		os.Exit(1)
	}
}

func listenTCP(ctx context.Context, addr string) (net.Listener, error) {
	return new(net.ListenConfig).Listen(ctx, "tcp", addr) //nolint:wrapcheck // run wraps it
}

// run serves until SIGTERM or SIGINT, then shuts down gracefully.
func run(getenv func(string) string, stdout io.Writer, listen listenFunc) error {
	cfg, err := config.Load(getenv)
	if err != nil {
		return fmt.Errorf("config: %w", err)
	}
	logger := logging.New(stdout, cfg.LogLevel)
	slog.SetDefault(logger)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	// signal.Notify instead of signal.NotifyContext: the goroutine must tell a signal
	// from a normal return of run, and it logs which signal arrived. Registered before
	// the listener opens, so a signal can never hit the default handler while serving.
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM, os.Interrupt)
	defer signal.Stop(sigs)
	go func() {
		select {
		case sig := <-sigs:
			// Restore the default handling: a second Ctrl-C or SIGTERM kills the
			// process at once instead of waiting for the shutdown timeout.
			signal.Stop(sigs)
			logger.Info("shutting down",
				"signal", sig.String(), "timeout", cfg.ShutdownTimeout.String())
			cancel()
		case <-ctx.Done():
		}
	}()

	ln, err := listen(ctx, cfg.HTTPAddr)
	if err != nil {
		return fmt.Errorf("listen: %w", err)
	}
	logger.Info("listening", "addr", ln.Addr().String())

	if err := server.Run(ctx, server.New(server.NewRouter(logger), logger), ln, cfg.ShutdownTimeout); err != nil {
		return fmt.Errorf("server: %w", err)
	}
	logger.Info("stopped")
	return nil
}
