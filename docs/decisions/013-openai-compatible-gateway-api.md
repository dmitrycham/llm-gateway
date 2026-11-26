# ADR-013: The gateway's external API is a subset of OpenAI Chat Completions

- Status: Accepted
- Date: 2026-11-26

## Context

External applications call the Go Gateway directly (ADR-003). Most of them already use
an OpenAI SDK or a framework built on it. The compatibility criterion of spec §7.0 is
that a standard OpenAI client makes a regular and a streaming request after changing
only the base URL and the key.

Behind the gateway are providers with different APIs: the Claude API, OpenAI-compatible
APIs and Ollama (FR-05). They accept different request fields and value ranges, and
the gateway may fall back from one to another (ADR-003). A field that one provider
honours and another ignores would make the answer depend on which provider was up.

The contract itself is in `api/openapi/gateway.yaml` (LGW-24). This ADR records why it
has that shape and how request fields map to each provider.

## Decision

The public API is `POST /v1/chat/completions`, a strict subset of the OpenAI Chat
Completions API.

- **Supported:** `model` (an alias configured for the application), text-only
  `messages` with roles `system`, `developer`, `user`, `assistant`,
  `max_completion_tokens` (or the legacy `max_tokens`), `stream`,
  `stream_options.include_usage`, and the sampling fields `temperature`, `top_p`,
  `stop`, `presence_penalty`, `frequency_penalty`, `seed`. `n` may only be `1`.
- **Closed schema.** Any other field, such as `functions`, is
  `400 unsupported_parameter` with `param` naming it. Nothing is silently ignored.
- **No-op values are accepted.** Many clients send defaults for features the gateway
  does not have: `tools: []`, `tool_choice: none`, `parallel_tool_calls`,
  `response_format: {type: text}`, `logprobs: false`, `store: false`. Rejecting them
  would break the "change only the base URL" goal. Any other value (a non-empty
  `tools`, structured output, `n > 1`) is `400 unsupported_parameter`.
- **Errors** keep the OpenAI shape the SDKs read (`message`, `type`, `code`, `param`)
  and add `retryable` and `request_id`. Validation errors are `400`, as in OpenAI.

**Providers must honour the request as sent.** Each model in the gateway's model
catalog declares which optional request fields it supports. Capabilities are set per
model, not per provider, because they differ between models of one provider. Before
routing, the gateway drops every candidate, including fallbacks, that cannot honour a
field the request sets (a `null` field counts as not set). It never drops, clamps or
rewrites a value. If no allowed candidate is left, the answer is
`400 unsupported_parameter` before any provider call and before any budget
reservation, and it is never stored as a result (ADR-009).

**Field mapping.** "Drop" means the candidate is removed when the field is set.

| Request field | Claude API | OpenAI-compatible | Ollama |
| --- | --- | --- | --- |
| `system`, `developer` messages | joined into the top-level `system` | `system` (`developer` where the model supports it) | `system` |
| `max_completion_tokens` / `max_tokens` | `max_tokens`; the configured default when not set, because the Claude API requires it | `max_completion_tokens` | `max_tokens` |
| `temperature`, `top_p` | drop | per model | sent as is |
| `stop` | `stop_sequences` | per model | sent as is |
| `presence_penalty`, `frequency_penalty` | drop | per model | sent as is |
| `seed` | drop | per model | sent as is |
| `name` on a message | drop | per model | per model |
| `user` | never sent; stored with usage | never sent | never sent |
| `stream`, `include_usage` | translated from Claude API events (below) | native | native |

- **Claude API sampling.** Current Claude API versions reject `temperature`, `top_p`
  and `top_k` on every model, even at their default values, and the API has no `seed`
  or penalties. So a Claude model is a candidate only for requests that set none of
  these fields.
- **Claude API streaming.** The gateway turns Claude API SSE events into OpenAI
  chunks: text deltas become `delta.content`; thinking blocks are not sent to the
  client, and their tokens are counted in the usage; `message_delta` gives the stop
  reason and the output tokens.
- **Claude API stop reasons.** `end_turn`, `stop_sequence` → `stop`;
  `max_tokens`, `model_context_window_exceeded` → `length`; `refusal` →
  `content_filter`. The same mapping applies to regular and streaming responses; in a
  stream the mapped reason is sent in the chunk with `finish_reason`, before the usage
  chunk and `[DONE]`. Any other stop reason is a provider error: `502` before the
  stream starts, a cut-off stream after it.
- **Usage.** Returned in the response and, with `include_usage`, in one extra chunk
  before `[DONE]`. When the provider does not report it, the usage is marked as
  estimated, never invented (FR-05).

## Consequences

Trade-off: standard OpenAI clients work without changes, and an answer never depends
on a provider quietly ignoring a parameter, in exchange for translating provider
formats in the gateway and fewer fallback options for requests that set sampling
fields.

Positive:

- OpenAI SDKs and frameworks need only a new base URL and key. Their retry logic works
  because the gateway sets `x-should-retry` and the request ID header.
- Unsupported features fail loudly with a `param`, so a client learns about them on
  the first call, not from strange answers.
- The capability check is one place for all providers; a new provider is a new set of
  catalog entries and an adapter.

Negative:

- Clients that always send `temperature` or `top_p`, as many frameworks do by default,
  never reach Claude models. Their requests go to other allowed providers or get
  `400`. This is documented for application owners.
- Translating Claude API streams to OpenAI chunks is code we own, and it must be tested
  against recorded event sequences, including a stream cut off mid-way.
- No tool calling, structured output, images or `n > 1` in this phase. Clients that
  need them must call a provider directly.
- The catalog's capability lists must be kept up to date when providers change their
  APIs; a wrong entry either hides a working model or sends a request that the
  provider rejects with `400`.

## Alternatives considered

- **Our own API shape (rejected).** Simpler to map to every provider, but every client
  would need a custom SDK, which fails the compatibility criterion.
- **Full OpenAI API compatibility (rejected).** Tools, images, structured output and
  `n > 1` each need provider-specific translation and their own tests. Too much for
  the first version; a subset that fails loudly is honest about the limits.
- **Ignore unsupported fields silently (rejected).** A request with `temperature: 0`
  would get a non-deterministic answer from a provider that ignores it, and the client
  would not know.
- **Drop or clamp unsupported sampling values per provider (rejected).** Same problem:
  the answer would depend on which provider served it, and a fallback would quietly
  change the request's meaning.
- **An Anthropic-compatible (`/v1/messages`) API as well (rejected for now).** Doubles
  the contract and the translation tests. It can be added later as a second route over
  the same core.
