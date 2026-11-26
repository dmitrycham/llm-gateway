// Package config loads the go-api settings from environment variables.
package config

import (
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"
)

// Prefix is the prefix of every go-api environment variable.
const Prefix = "GO_API_"

// Defaults. Port 8090: 8080/8081 belong to the gateway and 8000 to ai-service.
const (
	DefaultHTTPAddr        = ":8090"
	DefaultShutdownTimeout = 15 * time.Second
)

var logLevels = map[string]slog.Level{
	"debug": slog.LevelDebug,
	"info":  slog.LevelInfo,
	"warn":  slog.LevelWarn,
	"error": slog.LevelError,
}

// Config holds the go-api settings.
type Config struct {
	// HTTPAddr is the listen address (GO_API_HTTP_ADDR).
	HTTPAddr string
	// ShutdownTimeout bounds the wait for in-flight requests after SIGTERM
	// (GO_API_SHUTDOWN_TIMEOUT, a Go duration such as "15s"). Keep it below the
	// orchestrator's kill grace period (30 s by default in Kubernetes).
	ShutdownTimeout time.Duration
	// LogLevel is the minimum log level (GO_API_LOG_LEVEL: debug, info, warn, error).
	LogLevel slog.Level
}

// Load reads the settings through getenv (os.Getenv in production). An unset or empty
// variable takes its default; an invalid value is an error.
func Load(getenv func(string) string) (Config, error) {
	cfg := Config{
		HTTPAddr:        DefaultHTTPAddr,
		ShutdownTimeout: DefaultShutdownTimeout,
		LogLevel:        slog.LevelInfo,
	}
	var errs []error

	if v := getenv(Prefix + "HTTP_ADDR"); v != "" {
		cfg.HTTPAddr = v
	}
	if v := getenv(Prefix + "SHUTDOWN_TIMEOUT"); v != "" {
		d, err := time.ParseDuration(v)
		switch {
		case err != nil:
			errs = append(errs, fmt.Errorf("%sSHUTDOWN_TIMEOUT: %w", Prefix, err))
		case d <= 0:
			errs = append(errs, fmt.Errorf("%sSHUTDOWN_TIMEOUT: must be positive, got %q", Prefix, v))
		default:
			cfg.ShutdownTimeout = d
		}
	}
	if v := getenv(Prefix + "LOG_LEVEL"); v != "" {
		// Not slog.Level.UnmarshalText: it also accepts offsets such as "ERROR+1", which
		// would hide even the error that stops the service.
		if level, ok := logLevels[strings.ToLower(v)]; ok {
			cfg.LogLevel = level
		} else {
			errs = append(errs, fmt.Errorf("%sLOG_LEVEL: want debug, info, warn or error, got %q", Prefix, v))
		}
	}

	if err := errors.Join(errs...); err != nil {
		return Config{}, err
	}
	return cfg, nil
}
