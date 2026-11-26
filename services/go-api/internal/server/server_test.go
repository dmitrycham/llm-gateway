package server_test

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/dmitrycham/llm-gateway/services/go-api/internal/requestid"
	"github.com/dmitrycham/llm-gateway/services/go-api/internal/server"
)

const waitLimit = 5 * time.Second

var discard = slog.New(slog.DiscardHandler)

// errRefused is what memListener returns to a dial after Close, like ECONNREFUSED.
var errRefused = errors.New("connection refused")

// memListener is an in-memory net.Listener built on net.Pipe. The tests use it instead of
// TCP so that they run where binding a local port is not allowed; http.Server treats it
// like any other listener.
type memListener struct {
	conns chan net.Conn
	done  chan struct{}
	once  sync.Once
}

func newMemListener() *memListener {
	return &memListener{conns: make(chan net.Conn), done: make(chan struct{})}
}

func (l *memListener) Accept() (net.Conn, error) {
	select {
	case c := <-l.conns:
		return c, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}

func (l *memListener) Close() error {
	l.once.Do(func() { close(l.done) })
	return nil
}

func (l *memListener) Addr() net.Addr { return memAddr{} }

func (l *memListener) DialContext(ctx context.Context, _, _ string) (net.Conn, error) {
	select {
	case <-l.done:
		return nil, errRefused
	default:
	}
	srvConn, cliConn := net.Pipe()
	select {
	case l.conns <- srvConn:
		return cliConn, nil
	case <-l.done:
	case <-ctx.Done():
	}
	_ = srvConn.Close()
	_ = cliConn.Close()
	return nil, errRefused
}

type memAddr struct{}

func (memAddr) Network() string { return "mem" }
func (memAddr) String() string  { return "mem" }

func (l *memListener) client(keepAlive bool) *http.Client {
	return &http.Client{Transport: &http.Transport{
		DialContext:       l.DialContext,
		DisableKeepAlives: !keepAlive,
	}}
}

type result struct {
	status int
	header http.Header
	body   string
	err    error
}

func get(c *http.Client, path string) result {
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, "http://go-api"+path, http.NoBody)
	if err != nil {
		return result{err: err}
	}
	resp, err := c.Do(req)
	if err != nil {
		return result{err: err}
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	return result{status: resp.StatusCode, header: resp.Header, body: string(body), err: err}
}

func waitFor[T any](t *testing.T, ch <-chan T, what string) T {
	t.Helper()
	select {
	case v := <-ch:
		return v
	case <-time.After(waitLimit):
		t.Fatalf("timed out waiting for %s", what)
		panic("unreachable")
	}
}

func TestHealth(t *testing.T) {
	rec := httptest.NewRecorder()
	server.NewRouter(discard).ServeHTTP(rec, httptest.NewRequestWithContext(t.Context(), http.MethodGet, "/health/live", http.NoBody))

	if rec.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rec.Code)
	}
	if got := rec.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q", got)
	}
	if got := rec.Body.String(); got != `{"status":"ok"}`+"\n" {
		t.Errorf("body = %q", got)
	}
	if _, err := uuid.Parse(rec.Header().Get(requestid.Header)); err != nil {
		t.Errorf("X-Request-ID = %q, want a UUID", rec.Header().Get(requestid.Header))
	}
}

func TestUnknownRouteHasRequestID(t *testing.T) {
	rec := httptest.NewRecorder()
	server.NewRouter(discard).ServeHTTP(rec, httptest.NewRequestWithContext(t.Context(), http.MethodGet, "/nope", http.NoBody))

	if rec.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404", rec.Code)
	}
	if rec.Header().Get(requestid.Header) == "" {
		t.Error("404 response has no X-Request-ID")
	}
}

func TestNewSetsTimeouts(t *testing.T) {
	srv := server.New(http.NotFoundHandler(), discard)
	if srv.ReadHeaderTimeout <= 0 || srv.ReadTimeout <= 0 || srv.IdleTimeout <= 0 {
		t.Errorf("timeouts: header %v, read %v, idle %v; want all positive",
			srv.ReadHeaderTimeout, srv.ReadTimeout, srv.IdleTimeout)
	}
	if srv.WriteTimeout != 0 {
		t.Errorf("WriteTimeout = %v, want 0 (SSE responses stay open)", srv.WriteTimeout)
	}
}

// serve runs h on a memListener until the test ends.
func serve(t *testing.T, h http.Handler) *memListener {
	t.Helper()
	ln := newMemListener()
	ctx, cancel := context.WithCancel(context.Background())
	runErr := make(chan error, 1)
	go func() { runErr <- server.Run(ctx, server.New(h, discard), ln, waitLimit) }()
	t.Cleanup(func() {
		cancel()
		if err := waitFor(t, runErr, "Run to return"); err != nil {
			t.Errorf("Run() = %v, want nil", err)
		}
	})
	return ln
}

// A handler that panics after flushing part of the body must not reach the client as a
// complete 200: the connection breaks before the end of the body.
func TestPanicAfterPartialResponseBreaksConnection(t *testing.T) {
	r := server.NewRouter(discard)
	r.Get("/partial", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "partial")
		_ = http.NewResponseController(w).Flush()
		panic("boom")
	})

	if res := get(serve(t, r).client(false), "/partial"); res.err == nil {
		t.Errorf("got a complete response %+v, want a broken connection", res)
	}
}

// A handler that staged headers and panicked before writing must give the client a clean
// 500: a stale Content-Length would make the read fail with an unexpected EOF.
func TestPanicBeforeWriteSendsClean500(t *testing.T) {
	r := server.NewRouter(discard)
	r.Get("/staged", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", "10")
		w.Header().Set("Content-Encoding", "gzip")
		panic("boom")
	})

	res := get(serve(t, r).client(false), "/staged")
	if res.err != nil || res.status != http.StatusInternalServerError {
		t.Fatalf("got %+v, want a clean 500", res)
	}
	if v := res.header.Get("Content-Encoding"); v != "" {
		t.Errorf("Content-Encoding = %q survived into the 500", v)
	}
	if res.header.Get(requestid.Header) == "" {
		t.Error("500 response has no X-Request-ID")
	}
}

// slowRouter adds GET /slow, which signals started and then blocks until release closes.
func slowRouter(started, release chan struct{}) http.Handler {
	r := server.NewRouter(discard)
	r.Get("/slow", func(w http.ResponseWriter, _ *http.Request) {
		close(started)
		<-release
		_, _ = io.WriteString(w, "done")
	})
	return r
}

// TestRunGracefulShutdown is the LGW-18 shutdown criterion: after the signal the
// in-flight request completes and new requests are refused.
func TestRunGracefulShutdown(t *testing.T) {
	started, release := make(chan struct{}), make(chan struct{})
	ln := newMemListener()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	runErr := make(chan error, 1)
	go func() { runErr <- server.Run(ctx, server.New(slowRouter(started, release), discard), ln, waitLimit) }()

	inflight := make(chan result, 1)
	go func() { inflight <- get(ln.client(true), "/slow") }()
	waitFor(t, started, "the in-flight request to start")

	cancel() // what the SIGTERM handler in main does
	waitFor(t, ln.done, "the listener to close")

	if res := get(ln.client(false), "/health/live"); !errors.Is(res.err, errRefused) {
		t.Errorf("new request after shutdown: %+v, want errRefused", res)
	}
	select {
	case err := <-runErr:
		t.Fatalf("Run returned before the in-flight request finished: %v", err)
	default:
	}

	close(release)
	res := waitFor(t, inflight, "the in-flight response")
	if res.err != nil || res.status != http.StatusOK || res.body != "done" {
		t.Errorf("in-flight request = %+v, want 200 done", res)
	}
	if err := waitFor(t, runErr, "Run to return"); err != nil {
		t.Errorf("Run() = %v, want nil", err)
	}
}

func TestRunShutdownTimeout(t *testing.T) {
	started, release := make(chan struct{}), make(chan struct{})
	defer close(release)
	ln := newMemListener()
	ctx, cancel := context.WithCancel(context.Background())

	runErr := make(chan error, 1)
	go func() {
		runErr <- server.Run(ctx, server.New(slowRouter(started, release), discard), ln, 50*time.Millisecond)
	}()

	inflight := make(chan result, 1)
	go func() { inflight <- get(ln.client(true), "/slow") }()
	waitFor(t, started, "the in-flight request to start")
	cancel()

	if err := waitFor(t, runErr, "Run to return"); !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("Run() = %v, want context.DeadlineExceeded", err)
	}
	if res := waitFor(t, inflight, "the in-flight request to fail"); res.err == nil {
		t.Errorf("in-flight request = %+v, want a connection error", res)
	}
}

func TestRunServeError(t *testing.T) {
	ln := newMemListener()
	_ = ln.Close()

	err := server.Run(context.Background(), server.New(server.NewRouter(discard), discard), ln, waitLimit)
	if !errors.Is(err, net.ErrClosed) {
		t.Errorf("Run() = %v, want net.ErrClosed", err)
	}
}
