# ADR-004: SSE in the gateway in Phase 1; the RAG widget stays synchronous

- Status: Accepted
- Date: 2026-11-24
- Supersedes: the v1 draft of this decision, where streaming was planned for Phase 2.
  Now SSE in the Go Gateway moves to Phase 1, and the RAG widget stays synchronous.

## Context

The gateway exposes an OpenAI-compatible `POST /v1/chat/completions` (ADR-013). Its
compatibility criterion is that a standard OpenAI client can make a normal and a
streaming request after changing only the base URL and the key. Most clients and chat
UIs use `stream: true`, so a gateway without streaming is not compatible in practice.

The RAG widget is a different client. Its answer is valid only after the server checks
the citations on the full text (FR-04):

- the model may cite only `[S1]…[Sn]` labels;
- the server keeps labels that match fragments in the context and removes the others;
- `grounding` (`cited`, `uncited`, `no_answer`) is known only at the end.

If the widget showed tokens as they arrive, the user would see labels that are later
removed and an answer that is later marked as unconfirmed. Text that the user has
already read cannot be taken back.

## Decision

**Gateway (Phase 1).** `POST /v1/chat/completions` supports `stream: true` with
Server-Sent Events in the OpenAI chunk format.

- All checks run before the first byte of the response: request validation, API key,
  quotas, data policy, budget reservation. Any failure at this point is a normal JSON
  error with the shared error contract (spec §7.4).
- After the response headers are sent, the HTTP status cannot change, so a failed
  stream just stops. `finish_reason` and `data: [DONE]` are separate events: with
  `include_usage`, a usage chunk comes between them, and the connection can break
  there. A stream is complete only if the client saw both:
  `complete = saw_finish_reason && saw_done`. Any other end is a partial answer.
  The gateway records it in the same way.
- Fallback to another model is allowed only before the first chunk (ADR-003).
- The response sets `Content-Type: text/event-stream`, `Cache-Control: no-cache` and
  `X-Accel-Buffering: no`, and flushes after every chunk, so proxies and the Ingress
  (Phase 2) do not buffer the stream.
- When the client disconnects, the gateway cancels the provider call through the
  request context, records the attempt with the usage it actually received, and
  settles the reservation.
- Usage is sent in the last chunk when the client asks for
  `stream_options.include_usage` and the provider reports it. If the provider does not
  report it, the gateway stores an estimate with an explicit status, not a made-up
  number.
- The Claude API stream events are translated into OpenAI chunks in the gateway.

**RAG widget.** The widget keeps the synchronous `POST /api/v1/sessions/{id}/messages`.
Go API → Python → Go Gateway → provider calls are all non-streaming (ADR-001). If the
widget's request times out (`504 timeout`), the client gets the result later with
`GET /api/v1/sessions/{id}/messages/{message_id}` or by retrying with the same
`Idempotency-Key`. A replayed answer is checked against the source map stored with it,
not against a new retrieval (ADR-009).

## Consequences

Trade-off: external clients get streaming from day one and the widget shows only
checked answers, in exchange for a slower first visible token in the widget and two
response modes in the gateway.

Positive:

- The gateway passes the OpenAI SDK compatibility test for normal and streaming
  requests in Phase 1.
- Citation checking stays simple: it runs once on the full answer.
- The internal Go ↔ Python link stays plain request/response JSON.

Negative:

- The widget user waits for the whole answer (several seconds on a local model).
  The UI must show a clear "thinking" state.
- The gateway has two code paths (normal and stream), and the stream path has its own
  failure modes: errors after the first chunk, client disconnects, missing usage.
  Tests must cover a break before the first chunk, after the first chunk, and between
  `finish_reason` and `[DONE]` (the result must be "incomplete").
- Budget settlement for streams happens at the end of the stream or on disconnect, so
  a reservation is held for the whole stream.

## Alternatives considered

- **Stream to the widget and check citations at the end (rejected).** The user would
  see labels that are later removed and could read an answer before it is marked as
  `uncited`. Fixing text that is already on the screen is confusing.
- **WebSocket instead of SSE (rejected).** OpenAI clients expect SSE. A WebSocket adds
  a second protocol, needs its own proxy settings, and gives nothing extra for a
  one-way stream.
- **Streaming only in Phase 2 (v1, rejected).** Without `stream: true` the gateway fails
  its own compatibility criterion, and the stream failure modes (break after the first
  chunk, fallback only before it) would shape the budget and attempt model too late.
