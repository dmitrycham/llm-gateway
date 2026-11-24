# ADR-009: Generation results are stored by the Go Gateway with a lease per idempotency key

- Status: Accepted
- Date: 2026-11-24
- Supersedes: the v1 draft of this decision, where Python stored generation results in
  `ai.generation_requests`. Now result storage and lease ownership move from Python to
  the Go Gateway.

## Context

A widget message is a paid model call. The client retries with the same
`Idempotency-Key` after a timeout or a lost connection (spec §7.5). Without a stored
result, each retry pays for a new generation.

The dangerous case is "the model answered, but the caller crashed before it saved the
answer". In v2 the gateway owns model calls, retries and budgets (ADR-003). If Python
or the Go API stored the result, the payment and the result would be in different
services and different transactions, with a wider window where a paid result is lost.

Two handlers can also receive the same key at the same time: a client retry while the
first request is still running, or a retry after a lease has expired.

## Decision

The Go Gateway stores generation results in PostgreSQL, in its own schema, in
`gateway.generation_requests`. Python does not store them.

**Key and scope.**

- Widget path: the Go API computes `sha256(session_id || Idempotency-Key)` and the
  request hash of the client body, and sends them to Python. Python forwards them to
  the gateway as `X-Idempotency-Key-Hash` and `X-Idempotency-Request-Hash` on the
  `generate` call. The request hash comes from the client request, not from the
  gateway body, because retrieval on a retry may build a different context for the
  same question.
- External clients may send `Idempotency-Key` on `POST /v1/chat/completions`. The
  gateway scopes it by the application of the API key and hashes the canonical JSON
  body as the request hash.
- The record is found by `(workspace_id, idem_hash)`. `workspace_id` comes from the
  verified caller (API key or service token), never from the request body alone.

**Source map for RAG replays.** A stored answer is valid only with the context it was
generated from. On a retry, retrieval may return other fragments in another order, so
`[S1]` could point to a different document than in the first answer.

- Before the paid call, Python builds the source map
  `label → (document_id, version_id, chunk_id, title_at_generation)` and sends it with
  the `generate` call as an opaque `client_metadata` object (size limit in
  `gateway.yaml`).
- The gateway stores `client_metadata` with the result and does not read it.
- On a replay the gateway returns the stored answer with its original
  `client_metadata`. Python checks citations against this stored map, not against a
  new retrieval.
- The source snapshot in the history (FR-04) takes the title from
  `title_at_generation`, so a renamed or deleted document keeps the title the answer
  was written from.
- Before it gives out a link, the server checks that the source is still available to
  the client. For a deleted document it returns no link, and the UI shows the
  "source deleted" mark from FR-04.
- Tests: swap documents A and B in the ranking between the first call and the retry,
  and `[S1]` must still lead to A. Rename and then delete A before the retry: the
  snapshot keeps the original title, and the deleted source has no link.

**Lookup before the first paid step.** A committed answer must be available on a retry
even when the budget is now exhausted or a provider is down.

- Before retrieval, `condense` and `embed_query`, Python calls the gateway lookup
  (`GET /internal/v1/generations/lookup`, details in `gateway.yaml`) with
  `X-Idempotency-Key-Hash` and `X-Idempotency-Request-Hash`. The gateway looks up the
  row by `(workspace_id, idem_hash)`. If its `request_hash` differs, it returns
  `422 idempotency_key_reuse` before inspecting the state. Otherwise:
  - `completed` → the stored answer with its `client_metadata`; Python replays it and
    makes no other calls;
  - `failed`, final → the stored error;
  - `processing`, lease not expired → `409 request_in_progress`;
  - no record, expired lease, or retryable failure → `404 not_found`; Python runs the
    full pipeline.
- The lookup and the replay of a committed result reserve no budget and check no
  quotas. The same order applies inside `generate`: the idempotency check runs before
  quota and budget checks, so `budget_exceeded` never hides a committed answer.
- The lookup is a fast path, not a lock. If another handler commits between the lookup
  and `generate`, the `generate` claim below still returns the stored result.
- Tests: a lookup with the same key and a different body returns `422`, and Python
  makes no paid calls. Commit a result, exhaust the budget, retry, and the client
  gets the original answer while the provider call counter stays the same. The
  stage 5 acceptance scenario also passes: the Go API crashes between the Python answer and saving it,
  a retry after 20 minutes makes no provider call.

**States.** `processing → completed | failed`, with `retryable` on failures.

| Record state | Same request hash | Different request hash |
| --- | --- | --- |
| none | create `processing` with a new `lease_token`, call the model | — |
| `processing`, lease not expired | `409 request_in_progress` + `Retry-After` | `422 idempotency_key_reuse` |
| `processing`, lease expired | take over with a new `lease_token`, call again | `422 idempotency_key_reuse` |
| `completed` | return the stored result, no provider call | `422 idempotency_key_reuse` |
| `failed`, retryable | take over with a new `lease_token`, call again | `422 idempotency_key_reuse` |
| `failed`, final | return the stored error | `422 idempotency_key_reuse` |

Create or take over in one statement:

```sql
INSERT INTO gateway.generation_requests AS g
  (workspace_id, idem_hash, request_hash, state, lease_token, lease_until,
   attempts, expires_at)
VALUES ($1, $2, $3, 'processing', gen_random_uuid(), now() + $4::interval,
        1, now() + interval '24 hours')
ON CONFLICT (workspace_id, idem_hash) DO UPDATE
SET state       = 'processing',
    lease_token = gen_random_uuid(),
    lease_until = now() + $4::interval,
    attempts    = g.attempts + 1
WHERE g.request_hash = EXCLUDED.request_hash
  AND ((g.state = 'failed' AND g.retryable)
    OR (g.state = 'processing' AND g.lease_until < now()))
RETURNING lease_token;
-- 0 rows → read the record: other request_hash → 422;
--          completed → stored result; processing → 409; final failure → stored error
```

**Rules.**

- **Fencing.** The result is written only with
  `WHERE lease_token = $my_token AND state = 'processing'`. A handler that lost its
  lease does not save its result.
- The result, the budget settlement and the usage records of the attempt are written
  in **one transaction**. They are all in the gateway database.
- The lease covers the request deadline plus a short grace period. The exact default
  goes to spec §10.
- TTL is 24 hours, the same as the idempotency key in the Go API. A periodic job
  deletes expired records, but never a record with a live lease:
  `expires_at <= now() AND (state <> 'processing' OR lease_until <= now())`.
  A takeover keeps the original `expires_at`, so without the lease condition the
  cleanup could delete a record during a provider call, and the next retry would
  start a new paid call. Test: run the cleanup right after a takeover near the TTL.
- **Streams.** In Phase 1, `stream: true` together with an idempotency key is rejected
  with `400 invalid_request`. The check runs before SSE headers are sent, before the
  budget is reserved and before any provider call, so the client gets a normal JSON
  error (spec §7.4), not a broken stream. This rule goes into `gateway.yaml`.
- For the synchronous widget call `POST /api/v1/sessions/{id}/messages`, the
  `Idempotency-Key` stays required.
- Only `generate` results are stored. `condense` and `embed_*` calls are not keyed;
  a retry repeats them only when the lookup finds no committed result.
- Python passes `409 request_in_progress` up unchanged. The Go API returns `409` with
  `Retry-After` to the client and does not change its own key state.

**Guarantee.** Only the current lease holder can commit the result. Replaying a
committed result makes no paid calls and does not depend on the budget. Calls with an
unknown outcome, and RAG steps that ran before a result was committed, may incur
charges.

**Limits of the guarantee.** There is no "paid at most once" guarantee and no
"never processed in parallel" guarantee:

- After a lease expires, a new handler takes over while the old one may still be
  waiting for the provider. Two provider calls for one key can run at the same time;
  only the current lease holder commits (fencing), and the late result is dropped.
- The provider may return a paid answer just before the gateway crashes and before
  the PostgreSQL commit. That answer is lost, and the next retry pays again. The
  reservation reaper records the lost attempt as `outcome = unknown` with the reserved
  amount.
- If the lookup misses because the first handler commits a moment later, the retry
  has already paid for `condense` and `embed_query` before `generate` returns the
  stored answer.

This decision replaces the v1 text in spec §7.5 ("Python stores the generation result")
and §7.6. Those sections will be rewritten (LGW-26); until then, this ADR takes priority.

## Consequences

Trade-off: a committed result survives a crash of the Go API, Python or Redis, and
only the current lease holder can commit it, in exchange for a database write on every
generation and a lease to manage.

Positive:

- Result, budget settlement and usage are committed in one transaction. Once the
  commit succeeds, the answer is never paid for again within the TTL.
- Replaying a committed result makes no paid calls, so it works even when the budget
  is exhausted or the provider is down.
- The source map stored with the answer keeps citations and source titles correct on
  replay, even after a document is renamed or deleted.
- External gateway clients get the same idempotency as the widget.
- Redis is not needed for idempotency (spec §9.3).

Negative:

- One more insert and update per generation in PostgreSQL.
- Python must forward two extra headers and make one more call (the lookup) per
  widget message. Contract tests must check that the headers reach the gateway.
- The gateway API gets an extra endpoint, and the idempotency check must run before
  quota and budget checks in both the lookup and `generate`.
- After a lease expires, two provider calls for one key can run in parallel and both
  can be paid. A longer lease makes this rarer but keeps clients on `409` longer.
- The gateway stores opaque `client_metadata` for its callers. It must be
  size-limited and never interpreted by the gateway. Source titles make it larger.
- Streaming requests have no idempotency in Phase 1.
- The lease length is a tuning value: too short gives duplicate paid calls, too long
  keeps clients waiting with `409` after a crash.

## Alternatives considered

- **Python stores results in `ai.generation_requests` (v1, rejected).** It worked when
  Python owned provider calls. In v2 the payment is in the gateway, so the result and
  the settlement would be in different services and transactions.
- **Store results in Redis (rejected).** Redis can lose data on restart and is
  optional in degraded mode (spec §9.3). A lost result means a second payment.
- **Do not store results; rely on the Go API key table only (rejected).** The Go API
  key table cannot hold a result that was paid for but never returned to the Go API.
  Every crash after the provider call would lead to a second paid call.
- **Replay stored streams (rejected for Phase 1).** It needs storing chunks and replay
  semantics for partial streams. Too much for Phase 1; rejected with a clear `400`.
