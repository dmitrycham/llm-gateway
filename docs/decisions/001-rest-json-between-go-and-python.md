# ADR-001: REST/JSON between Go and Python, not gRPC

- Status: Accepted
- Date: 2026-11-24

## Context

The system has three services: the Go API (public and admin API, widget sessions),
the Python RAG service (retrieval, context assembly, indexing, evals) and the Go AI Gateway
(model calls). The RAG path is Go API → Python → Go Gateway → provider, so every
user question crosses two internal service boundaries.

We need an internal protocol that:

- has a contract that both languages can check in CI;
- is easy to debug by hand during development;
- carries a deadline and a request ID across both hops, so Python stops work
  when the Go caller has already given up.

The public API and the gateway API are already HTTP/JSON (OpenAPI and an
OpenAI-compatible `/v1/chat/completions`, ADR-013).

## Decision

Internal calls between Go and Python use REST over HTTP with JSON bodies.

- The contract is the committed file `api/openapi/internal-ai.yaml` (source of truth, ADR-005).
  Go generates its client from it; CI compares the FastAPI schema export with it.
- Every internal call carries these headers:
  - `X-Request-ID`: created at the Go entry point and passed through every hop and log line;
  - `X-Request-Deadline-Ms`: remaining time budget in milliseconds, minus a safety margin;
  - `traceparent`: W3C trace context when tracing is on.
- Python turns `X-Request-Deadline-Ms` into a local deadline on a monotonic clock and
  wraps the whole handler in `asyncio.timeout(...)`. It passes the remaining time on
  to the gateway in the same header.
- Streaming is not needed on the Go ↔ Python link: the widget is synchronous because
  citations are checked on the full answer (ADR-004). If streaming is needed later,
  it will be SSE over the same REST endpoints, not a protocol change.

## Consequences

Trade-off: easy debugging and one contract format for all APIs, in exchange for
hand-made deadline and cancellation handling that gRPC would give for free.

Positive:

- Any internal call can be reproduced with `curl` and read in logs as plain JSON.
- One contract tool chain (OpenAPI, `oapi-codegen`, `oasdiff`, contract tests) for the
  public, internal and gateway APIs.
- No protobuf build step in two languages.

Negative:

- Deadline propagation is our code. A service that forgets to read or forward
  `X-Request-Deadline-Ms` keeps working after the caller has gone. Contract tests and a
  shared middleware on each side must cover it.
- A closed client connection does not cancel a normal FastAPI handler. Only the explicit
  deadline stops the work.
- JSON is larger and slower to parse than protobuf. At this project's load this is not
  the bottleneck: model latency is seconds, serialization is microseconds.

## Alternatives considered

- **gRPC (rejected).** Built-in deadlines, cancellation and streaming, and a strict
  schema. Rejected because it adds a second contract system next to OpenAPI, needs
  protobuf tooling in Go and Python, and makes manual debugging harder
  (`grpcurl` and reflection instead of `curl`). Its main advantage, deadline
  propagation, is covered by one header and a middleware.
- **Message queue between Go and Python for chat requests (rejected).** The chat path is
  request/response with a user waiting. A queue adds latency and a broker as a failure
  point without adding value. Asynchronous work (indexing) has its own queue (ADR-002).
