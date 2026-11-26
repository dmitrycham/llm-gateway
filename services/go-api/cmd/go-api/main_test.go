package main

import (
	"bytes"
	"context"
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

func (l *idleListener) Addr() net.Addr { return &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 8090} }

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

// TestRunStopsOnSignal sends a real signal to the test process. If run ever stops
// handling it, the default action kills the test binary, so the regression cannot pass.
func TestRunStopsOnSignal(t *testing.T) {
	prev := slog.Default()
	t.Cleanup(func() { slog.SetDefault(prev) })

	cases := []struct {
		sig  syscall.Signal
		name string
	}{
		{syscall.SIGTERM, "terminated"},
		{syscall.SIGINT, "interrupt"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sink := &logSink{listening: make(chan struct{})}
			getenv := func(k string) string {
				return map[string]string{"GO_API_SHUTDOWN_TIMEOUT": "2s"}[k]
			}
			listen := func(context.Context, string) (net.Listener, error) {
				return &idleListener{done: make(chan struct{})}, nil
			}

			runErr := make(chan error, 1)
			go func() { runErr <- run(getenv, sink, listen) }()

			select {
			case <-sink.listening:
			case err := <-runErr:
				t.Fatalf("run returned before listening: %v", err)
			case <-time.After(waitLimit):
				t.Fatal("timed out waiting for the listening log line")
			}

			if err := syscall.Kill(syscall.Getpid(), tc.sig); err != nil {
				t.Fatalf("kill: %v", err)
			}

			select {
			case err := <-runErr:
				if err != nil {
					t.Fatalf("run() = %v, want nil", err)
				}
			case <-time.After(waitLimit):
				t.Fatal("run did not return after the signal")
			}

			out := sink.String()
			for _, want := range []string{
				`"msg":"shutting down","signal":"` + tc.name + `"`,
				`"msg":"stopped"`,
			} {
				if !strings.Contains(out, want) {
					t.Errorf("log output lacks %s:\n%s", want, out)
				}
			}
		})
	}
}

func TestRunConfigError(t *testing.T) {
	prev := slog.Default()
	t.Cleanup(func() { slog.SetDefault(prev) })

	getenv := func(k string) string {
		return map[string]string{"GO_API_LOG_LEVEL": "loud"}[k]
	}
	listen := func(context.Context, string) (net.Listener, error) {
		t.Fatal("listen called despite a config error")
		return nil, nil
	}
	err := run(getenv, &bytes.Buffer{}, listen)
	if err == nil || !strings.Contains(err.Error(), "GO_API_LOG_LEVEL") {
		t.Errorf("run() = %v, want a config error naming GO_API_LOG_LEVEL", err)
	}
}
