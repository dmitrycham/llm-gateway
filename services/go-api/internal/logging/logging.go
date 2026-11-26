// Package logging builds the service logger and the HTTP logging middleware.
package logging

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5/middleware"

	"github.com/dmitrycham/llm-gateway/services/go-api/internal/requestid"
)

// RequestIDKey is the log attribute that holds the request ID.
const RequestIDKey = "request_id"

// New returns a JSON logger that adds request_id to every record logged with a
// context that carries one (the *Context methods, e.g. InfoContext).
//
// Do not call WithGroup on this logger yet: request_id is added to the record, so it
// would land inside the group instead of at the top level where log searches expect it
// (ADR-011). The fix (replaying groups after adding the ID) belongs to the structured
// logging work. Do not pass a request_id attribute yourself either: it would be
// duplicated.
func New(w io.Writer, level slog.Level) *slog.Logger {
	return slog.New(NewHandler(slog.NewJSONHandler(w, &slog.HandlerOptions{Level: level})))
}

// NewHandler wraps next so that records get request_id from their context.
func NewHandler(next slog.Handler) slog.Handler {
	return contextHandler{next: next}
}

type contextHandler struct {
	next slog.Handler
}

func (h contextHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return h.next.Enabled(ctx, level)
}

func (h contextHandler) Handle(ctx context.Context, r slog.Record) error {
	if id := requestid.FromContext(ctx); id != "" {
		r = r.Clone()
		r.AddAttrs(slog.String(RequestIDKey, id))
	}
	return h.next.Handle(ctx, r) //nolint:wrapcheck // a decorator must not change the error
}

func (h contextHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return contextHandler{next: h.next.WithAttrs(attrs)}
}

func (h contextHandler) WithGroup(name string) slog.Handler {
	return contextHandler{next: h.next.WithGroup(name)}
}

// AccessLog logs one line per request after the response is written. It must run after
// requestid.Middleware so that the line carries the request ID.
//
// A panic that passes through (an aborted response) is still logged, at Warn level with
// aborted=true and the status and bytes actually sent, and then keeps propagating.
func AccessLog(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
			completed := false
			defer func() {
				status, level := ww.Status(), slog.LevelInfo
				switch {
				case !completed:
					level = slog.LevelWarn
				case status == 0:
					// The handler returned without writing, so net/http sends 200.
					status = http.StatusOK
				}
				logger.LogAttrs(r.Context(), level, "http request",
					slog.String("method", r.Method),
					slog.String("path", r.URL.Path),
					slog.Int("status", status),
					slog.Int("bytes", ww.BytesWritten()),
					slog.Duration("duration", time.Since(start)),
					slog.Bool("aborted", !completed),
				)
			}()
			next.ServeHTTP(ww, r)
			completed = true
		})
	}
}

// Recoverer turns a handler panic into a logged error and a 500 response. Unlike chi's
// middleware.Recoverer it logs through slog, so the record is JSON and has request_id.
//
// If the handler had already started the response, the status can no longer change.
// Returning normally would then let net/http finish a truncated body as a complete
// success, so Recoverer aborts the response instead and the client sees a broken
// connection.
func Recoverer(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Own wrapper, so that the check below does not depend on the middleware order.
			ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
			defer func() {
				rec := recover()
				if rec == nil {
					return
				}
				// net/http uses ErrAbortHandler to abort a response on purpose.
				if err, ok := rec.(error); ok && errors.Is(err, http.ErrAbortHandler) {
					panic(rec)
				}
				logger.LogAttrs(r.Context(), slog.LevelError, "panic in handler",
					slog.Any("panic", rec))
				if ww.Status() != 0 {
					panic(http.ErrAbortHandler)
				}
				// Headers the handler staged (Content-Length, Content-Encoding, ETag, ...)
				// describe a body that will never be sent; a stale Content-Length would make
				// the client fail with an unexpected EOF. Only the request ID stays.
				h := ww.Header()
				id := h.Get(requestid.Header)
				clear(h)
				if id != "" {
					h.Set(requestid.Header, id)
				}
				ww.WriteHeader(http.StatusInternalServerError)
			}()
			next.ServeHTTP(ww, r)
		})
	}
}
