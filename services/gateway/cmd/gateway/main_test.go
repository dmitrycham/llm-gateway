package main

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

const waitLimit = 5 * time.Second

// idleListener accepts nothing and blocks until closed, so run can serve without a port.
type idleListener struct {
	done chan struct{}
	once sync.Once
}

func (l *idleListener) Accept() (net.Conn, error) {
	<-l.done
	return nil, net.ErrClosed
}

func (l *idleListener) Close() error {
	l.once.Do(func() { close(l.done) })
	return nil
}

func (l *idleListener) Addr() net.Addr { return &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 8081} }

// logSink collects run's log output and reports when the "listening" line appears.
type logSink struct {
	mu        sync.Mutex
	buf       bytes.Buffer
	listening chan struct{}
	once      sync.Once
}

func (s *logSink) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if bytes.Contains(p, []byte(`"msg":"listening"`)) {
		s.once.Do(func() { close(s.listening) })
	}
	return s.buf.Write(p) //nolint:wrapcheck // bytes.Buffer.Write never fails
}

func (s *logSink) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.buf.String()
}

// TestRunStopsOnSignal sends a real SIGTERM to the test process. If run ever stops
// handling it, the default action kills the test binary, so the regression cannot pass.
func TestRunStopsOnSignal(t *testing.T) {
	sink := &logSink{listening: make(chan struct{})}
	var gotAddr string
	listen := func(_ context.Context, addr string) (net.Listener, error) {
		gotAddr = addr
		return &idleListener{done: make(chan struct{})}, nil
	}

	runErr := make(chan error, 1)
	go func() {
		runErr <- run(func(string) string { return "" }, slog.New(slog.NewJSONHandler(sink, nil)), listen)
	}()

	select {
	case <-sink.listening:
	case err := <-runErr:
		t.Fatalf("run returned before listening: %v", err)
	case <-time.After(waitLimit):
		t.Fatal("timed out waiting for the listening log line")
	}
	if gotAddr != defaultInternalAddr {
		t.Errorf("listen addr = %q, want %q", gotAddr, defaultInternalAddr)
	}

	if err := syscall.Kill(syscall.Getpid(), syscall.SIGTERM); err != nil {
		t.Fatalf("kill: %v", err)
	}
	select {
	case err := <-runErr:
		if err != nil {
			t.Fatalf("run() = %v, want nil", err)
		}
	case <-time.After(waitLimit):
		t.Fatal("run did not return after SIGTERM")
	}
	if out := sink.String(); !strings.Contains(out, `"msg":"stopped"`) {
		t.Errorf("log output lacks the stopped line:\n%s", out)
	}
}

func TestRunListenAddrFromEnv(t *testing.T) {
	errBoom := errors.New("boom")
	var gotAddr string
	listen := func(_ context.Context, addr string) (net.Listener, error) {
		gotAddr = addr
		return nil, errBoom
	}
	getenv := func(k string) string {
		return map[string]string{"GATEWAY_INTERNAL_ADDR": ":9999"}[k]
	}
	err := run(getenv, slog.New(slog.NewJSONHandler(&bytes.Buffer{}, nil)), listen)
	if !errors.Is(err, errBoom) {
		t.Errorf("run() = %v, want the listen error", err)
	}
	if gotAddr != ":9999" {
		t.Errorf("listen addr = %q, want :9999", gotAddr)
	}
}
