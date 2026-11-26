package requestid_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"

	"github.com/dmitrycham/llm-gateway/services/go-api/internal/requestid"
)

func TestNewIsUUIDv7(t *testing.T) {
	id := requestid.New()
	parsed, err := uuid.Parse(id)
	if err != nil {
		t.Fatalf("New() = %q: %v", id, err)
	}
	if parsed.Version() != 7 || parsed.String() != id {
		t.Errorf("New() = %q, want a canonical UUIDv7", id)
	}
	if requestid.New() == id {
		t.Error("two calls returned the same ID")
	}
}

func TestFromContextEmpty(t *testing.T) {
	if got := requestid.FromContext(context.Background()); got != "" {
		t.Errorf("FromContext() = %q, want empty", got)
	}
}

func TestMiddleware(t *testing.T) {
	const supplied = "0193a1f0-9d12-7c33-a1b2-6f8e9d0c1b2a"
	cases := map[string][]string{
		"absent":                  nil,
		"valid client ID ignored": {supplied},
		"invalid client ID":       {"nginx-abc"},
	}
	for name, incoming := range cases {
		t.Run(name, func(t *testing.T) {
			var seen, seenHeader string
			h := requestid.Middleware(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
				seen = requestid.FromContext(r.Context())
				seenHeader = r.Header.Get(requestid.Header)
			}))
			req := httptest.NewRequestWithContext(t.Context(), http.MethodGet, "/", http.NoBody)
			for _, v := range incoming {
				req.Header.Add(requestid.Header, v)
			}
			rec := httptest.NewRecorder()
			h.ServeHTTP(rec, req)

			got := rec.Header().Get(requestid.Header)
			if got != seen {
				t.Errorf("response header %q != context value %q", got, seen)
			}
			if seenHeader != seen {
				t.Errorf("request header %q != context value %q", seenHeader, seen)
			}
			if got == supplied {
				t.Error("the client-supplied ID was reused")
			}
			if parsed, err := uuid.Parse(got); err != nil || parsed.Version() != 7 {
				t.Errorf("request ID %q is not a UUIDv7", got)
			}
		})
	}
}
