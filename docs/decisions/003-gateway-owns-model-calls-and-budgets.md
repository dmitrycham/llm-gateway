# ADR-003: The Go Gateway owns model calls, retries, fallback and budgets

- Status: Accepted
- Date: 2026-11-24
- Supersedes: the v1 draft of this decision, where Python owned provider calls and
  budgets. That draft was never implemented.

## Context

Several parts of the system call language models:

- external clients through the OpenAI-compatible API (ADR-013);
- the RAG service (query condensation, query embeddings, answer generation);
- the indexing worker (document embeddings);
- the automatic evaluator.

Every paid call needs the same rules: the workspace data policy (`local_only`,
allowed providers), provider choice, retries with backoff, fallback, per-attempt usage
records and an atomic budget reservation. If these rules live in more than one service,
they drift apart, and money and retries are counted in more than one place.

## Decision

All model calls go through the Go AI Gateway. No other service talks to a model
provider directly.

The gateway:

- checks the API key, quotas and the workspace data policy;
- chooses the provider and model by the routing rule;
- reserves the budget in PostgreSQL for each attempt, including retries;
- calls the provider, retries with exponential backoff and full jitter, and falls back
  to a secondary model only when the policy allows it and only before the first
  streamed chunk;
- never falls back for embeddings: another model's vectors do not match the index;
- records every attempt (provider, model, tokens, cost, outcome) and settles the
  reservation.

The Python RAG service and worker keep the RAG logic: extraction, chunking, indexing,
retrieval, context assembly with citations, and evals. For generation and embeddings
they call the gateway and send:

- `workspace_id` and the operation type (`condense`, `generate`, `embed_query`,
  `embed_document`);
- `X-Request-ID` received from the Go API (or created by the worker);
- `X-Request-Deadline-Ms` with the time that is left (ADR-001).

The gateway API for internal callers is defined in `api/openapi/gateway.yaml`.

This decision replaces the v1 rules in spec sections 4.1–4.4, 6.1–6.4, 7.2 and 7.6 that
say "only the Python provider layer retries" and "Python owns budgets". Those sections
will be rewritten; until then, this ADR and spec section 4.0 take priority.

## Consequences

Trade-off: one place for money, retries and data policy, in exchange for an extra
network hop (Go API → Python → Go Gateway → provider) that must carry the deadline and
the request ID through both internal hops.

Positive:

- Budget checks, usage accounting and the `local_only` policy have one implementation
  and one set of tests, so they cannot drift between services.
- Retries happen at exactly one level. Python does not retry gateway calls that may
  already have been paid for, and the gateway decides from the remaining deadline
  whether a new attempt is still useful.
- External clients, RAG and evals get the same routing and cost reports, which the
  three-mode routing comparison in the evals depends on.
- Python has no provider SDKs and no provider keys.

Negative:

- One more network hop on the RAG path adds a few milliseconds and one more failure
  point. Model latency is seconds, so the hop does not change user-visible latency much.
- The gateway is on the critical path of every feature that uses a model. If it is
  down, chat, indexing and evals stop.
- Deadline and `X-Request-ID` must pass through two hops. If Python drops the deadline,
  the gateway can keep a paid call running after the user has gone. Contract tests must
  check that both headers arrive at the gateway.
- Python needs a gateway client and a fake for tests.

## Alternatives considered

- **Python owns provider calls and budgets (v1, rejected).** It works for RAG, but
  external clients of the gateway would need a second implementation of policies,
  retries and budgets in Go. Two implementations of money logic drift apart.
- **Each service calls providers directly and shares budget tables (rejected).**
  It removes the extra hop, but retries, fallback and the data policy would still be
  implemented in two languages. A shared table does not stop two services from both
  retrying the same paid call.
- **An off-the-shelf LLM proxy such as LiteLLM (rejected).** It gives routing and
  budgets quickly. But the gateway is the product of this project, and the goal is to
  build and operate it in Go: per-attempt reservation in PostgreSQL, the `local_only`
  policy and stream translation are the parts the project wants to own and test.
