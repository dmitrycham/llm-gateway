// Package requestid creates the X-Request-ID of each request.
//
// The Go API is the public entry point, so it always creates the ID itself (spec §4.2,
// ADR-001) and never trusts a client value: the ID is stored with messages, audit records,
// budget reservations and usage events, and a client-chosen value could collide with the
// rows of other requests.
//
// chi's middleware.RequestID is not used: it creates "host/random-000001" IDs, while the
// contracts require a UUID or ULID.
package requestid

import (
	"context"
	"net/http"

	"github.com/google/uuid"
)

// Header is the response header that carries the request ID.
const Header = "X-Request-ID"

type ctxKey struct{}

// New returns a new UUIDv7. Time-ordered IDs keep log lines and database rows of one
// period close together.
func New() string {
	// NewV7 fails only when crypto/rand fails, and since Go 1.24 crypto/rand crashes the
	// program instead of returning an error, so Must never panics in practice.
	return uuid.Must(uuid.NewV7()).String()
}

// NewContext returns a copy of ctx that carries id.
func NewContext(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, ctxKey{}, id)
}

// FromContext returns the request ID stored in ctx, or "" if there is none.
func FromContext(ctx context.Context) string {
	id, _ := ctx.Value(ctxKey{}).(string)
	return id
}

// Middleware creates a new request ID for every request and replaces any incoming
// X-Request-ID with it. The ID goes into the request context, the request header and the
// response header.
func Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := New()
		// Overwrite the request header too: code that reads it, or a reverse proxy that
		// copies incoming headers to the gateway, must never see the client's value.
		r.Header.Set(Header, id)
		w.Header().Set(Header, id)
		next.ServeHTTP(w, r.WithContext(NewContext(r.Context(), id)))
	})
}
