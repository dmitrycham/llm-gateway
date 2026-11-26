package config_test

import (
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/dmitrycham/llm-gateway/services/go-api/internal/config"
)

func env(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func TestLoadDefaults(t *testing.T) {
	cfg, err := config.Load(env(nil))
	if err != nil {
		t.Fatal(err)
	}
	want := config.Config{
		HTTPAddr:        config.DefaultHTTPAddr,
		ShutdownTimeout: config.DefaultShutdownTimeout,
		LogLevel:        slog.LevelInfo,
	}
	if cfg != want {
		t.Errorf("Load() = %+v, want %+v", cfg, want)
	}
}

func TestLoadOverrides(t *testing.T) {
	cfg, err := config.Load(env(map[string]string{
		"GO_API_HTTP_ADDR":        "127.0.0.1:9000",
		"GO_API_SHUTDOWN_TIMEOUT": "2500ms",
		"GO_API_LOG_LEVEL":        "DEBUG",
	}))
	if err != nil {
		t.Fatal(err)
	}
	want := config.Config{
		HTTPAddr:        "127.0.0.1:9000",
		ShutdownTimeout: 2500 * time.Millisecond,
		LogLevel:        slog.LevelDebug,
	}
	if cfg != want {
		t.Errorf("Load() = %+v, want %+v", cfg, want)
	}
}

func TestLoadInvalid(t *testing.T) {
	cases := map[string]map[string]string{
		"bad duration":      {"GO_API_SHUTDOWN_TIMEOUT": "15"},
		"zero duration":     {"GO_API_SHUTDOWN_TIMEOUT": "0s"},
		"negative duration": {"GO_API_SHUTDOWN_TIMEOUT": "-1s"},
		"bad level":         {"GO_API_LOG_LEVEL": "verbose"},
		"level offset up":   {"GO_API_LOG_LEVEL": "ERROR+1"},
		"level offset down": {"GO_API_LOG_LEVEL": "info-4"},
		"numeric level":     {"GO_API_LOG_LEVEL": "8"},
	}
	for name, vars := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := config.Load(env(vars))
			if err == nil {
				t.Fatal("Load() error = nil, want an error")
			}
			for k := range vars {
				if !strings.Contains(err.Error(), k) {
					t.Errorf("error %q does not name %s", err, k)
				}
			}
		})
	}
}

func TestLoadReportsAllErrors(t *testing.T) {
	_, err := config.Load(env(map[string]string{
		"GO_API_SHUTDOWN_TIMEOUT": "soon",
		"GO_API_LOG_LEVEL":        "loud",
	}))
	if err == nil || !strings.Contains(err.Error(), "SHUTDOWN_TIMEOUT") ||
		!strings.Contains(err.Error(), "LOG_LEVEL") {
		t.Errorf("Load() error = %v, want both variables named", err)
	}
}
