package logging_test

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/dmitrycham/llm-gateway/services/go-api/internal/logging"
	"github.com/dmitrycham/llm-gateway/services/go-api/internal/requestid"
)

const testID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

func decodeLines(t *testing.T, buf *bytes.Buffer) []map[string]any {
	t.Helper()
	var out []map[string]any
	for line := range strings.Lines(buf.String()) {
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("log line is not JSON: %q: %v", line, err)
		}
		out = append(out, m)
	}
	return out
}

// newRequest returns a request whose context carries testID, as requestid.Middleware
// would set it.
func newRequest(t *testing.T, method, path string) *http.Request {
	t.Helper()
	return httptest.NewRequestWithContext(requestid.NewContext(t.Context(), testID), method, path, http.NoBody)
}

// mustAbort runs h and fails unless it panics with http.ErrAbortHandler.
func mustAbort(t *testing.T, h http.Handler, r *http.Request) {
	t.Helper()
	defer func() {
		if rec := recover(); rec != http.ErrAbortHandler { //nolint:errorlint // identity check
			t.Errorf("recovered %v, want http.ErrAbortHandler", rec)
		}
	}()
	h.ServeHTTP(httptest.NewRecorder(), r)
}

func TestHandlerAddsRequestID(t *testing.T) {
	var buf bytes.Buffer
	logger := logging.New(&buf, slog.LevelInfo).With("component", "test")

	logger.InfoContext(requestid.NewContext(context.Background(), testID), "with id")
	logger.InfoContext(context.Background(), "without id")
	logger.DebugContext(context.Background(), "below level")

	lines := decodeLines(t, &buf)
	if len(lines) != 2 {
		t.Fatalf("got %d lines, want 2", len(lines))
	}
	if got := lines[0][logging.RequestIDKey]; got != testID {
		t.Errorf("request_id = %v, want %q", got, testID)
	}
	if got := lines[0]["component"]; got != "test" {
		t.Errorf("WithAttrs lost: component = %v", got)
	}
	if _, ok := lines[1][logging.RequestIDKey]; ok {
		t.Errorf("request_id present without one in the context: %v", lines[1])
	}
}

func TestAccessLog(t *testing.T) {
	var buf bytes.Buffer
	h := logging.AccessLog(logging.New(&buf, slog.LevelInfo))(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusTeapot)
			_, _ = w.Write([]byte("tea"))
		}))
	h.ServeHTTP(httptest.NewRecorder(), newRequest(t, http.MethodPost, "/brew"))

	lines := decodeLines(t, &buf)
	if len(lines) != 1 {
		t.Fatalf("got %d lines, want 1", len(lines))
	}
	want := map[string]any{
		"level":              "INFO",
		"msg":                "http request",
		"method":             "POST",
		"path":               "/brew",
		"status":             float64(http.StatusTeapot),
		"bytes":              float64(3),
		"aborted":            false,
		logging.RequestIDKey: testID,
	}
	for k, v := range want {
		if lines[0][k] != v {
			t.Errorf("%s = %v, want %v", k, lines[0][k], v)
		}
	}
}

func TestAccessLogImplicitOK(t *testing.T) {
	var buf bytes.Buffer
	h := logging.AccessLog(logging.New(&buf, slog.LevelInfo))(http.HandlerFunc(
		func(http.ResponseWriter, *http.Request) {}))
	h.ServeHTTP(httptest.NewRecorder(), newRequest(t, http.MethodGet, "/"))

	if got := decodeLines(t, &buf)[0]["status"]; got != float64(http.StatusOK) {
		t.Errorf("status = %v, want 200", got)
	}
}

// An aborted response is still logged, with what was actually sent.
func TestAccessLogAborted(t *testing.T) {
	var buf bytes.Buffer
	h := logging.AccessLog(logging.New(&buf, slog.LevelInfo))(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) {
			_, _ = w.Write([]byte("partial"))
			panic(http.ErrAbortHandler)
		}))
	mustAbort(t, h, newRequest(t, http.MethodGet, "/"))

	lines := decodeLines(t, &buf)
	if len(lines) != 1 {
		t.Fatalf("got %d lines, want 1", len(lines))
	}
	want := map[string]any{
		"level":              "WARN",
		"status":             float64(http.StatusOK),
		"bytes":              float64(len("partial")),
		"aborted":            true,
		logging.RequestIDKey: testID,
	}
	for k, v := range want {
		if lines[0][k] != v {
			t.Errorf("%s = %v, want %v", k, lines[0][k], v)
		}
	}
}

func TestRecoverer(t *testing.T) {
	var buf bytes.Buffer
	h := logging.Recoverer(logging.New(&buf, slog.LevelInfo))(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set(requestid.Header, testID)
			w.Header().Set("Content-Length", "10")
			w.Header().Set("Content-Encoding", "gzip")
			panic("boom")
		}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, newRequest(t, http.MethodGet, "/"))

	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", rec.Code)
	}
	for _, k := range []string{"Content-Length", "Content-Encoding"} {
		if v := rec.Header().Get(k); v != "" {
			t.Errorf("staged header %s = %q survived into the 500", k, v)
		}
	}
	if got := rec.Header().Get(requestid.Header); got != testID {
		t.Errorf("X-Request-ID = %q, want %q", got, testID)
	}
	line := decodeLines(t, &buf)[0]
	if line["level"] != "ERROR" || line["panic"] != "boom" || line[logging.RequestIDKey] != testID {
		t.Errorf("unexpected log line: %v", line)
	}
}

func TestRecovererRepanicsAbortHandler(t *testing.T) {
	h := logging.Recoverer(logging.New(&bytes.Buffer{}, slog.LevelInfo))(http.HandlerFunc(
		func(http.ResponseWriter, *http.Request) { panic(http.ErrAbortHandler) }))
	mustAbort(t, h, newRequest(t, http.MethodGet, "/"))
}

// A panic after the response started must abort it, not let it finish as a 200.
func TestRecovererAbortsStartedResponse(t *testing.T) {
	var buf bytes.Buffer
	h := logging.Recoverer(logging.New(&buf, slog.LevelInfo))(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) {
			_, _ = w.Write([]byte("partial"))
			panic("boom")
		}))
	mustAbort(t, h, newRequest(t, http.MethodGet, "/"))

	line := decodeLines(t, &buf)[0]
	if line["panic"] != "boom" || line[logging.RequestIDKey] != testID {
		t.Errorf("panic not logged before the abort: %v", line)
	}
}
