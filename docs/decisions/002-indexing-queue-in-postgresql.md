# ADR-002: Indexing queue in PostgreSQL, not Redis

- Status: Accepted
- Date: 2026-11-24

## Context

Uploading a document starts slow background work: text extraction, chunking,
embeddings through the gateway, and activation of the new index version. The Python
worker runs this work outside the request.

The upload handler already writes the document and its version to PostgreSQL. A job
must exist if and only if that write is committed:

- a committed document without a job is never indexed;
- a job without a committed document fails or indexes garbage.

Jobs must survive process crashes and must not be processed twice in parallel.
The expected load is small: tens to hundreds of documents per workspace, not
thousands of jobs per second.

## Decision

The indexing queue is a PostgreSQL table, `ai.ingestion_jobs`.

- The job row is inserted in the same transaction as the document version.
- A worker claims one job with `SELECT ... FOR UPDATE SKIP LOCKED`, sets
  `status = 'processing'`, a new `lease_token` and `locked_until`.
- A heartbeat extends `locked_until` only while `lease_token` still matches. If the
  heartbeat updates 0 rows, the worker stops the job at once.
- Every status change and index activation checks `lease_token` (fencing), so a stale
  worker cannot overwrite the result of a newer claim.
- A reaper moves jobs with an expired lease to `retry_wait` with backoff, or to `dead`
  after `max_attempts`.
- Chunk and embedding writes are idempotent (`ON CONFLICT DO NOTHING`), so a retry after
  a crash does not create duplicates.
- Workers poll every 1–2 seconds. `LISTEN/NOTIFY` is an optional optimisation.

## Consequences

Trade-off: atomic enqueue and no lost jobs, in exchange for lower throughput and
polling load on the main database.

Positive:

- No dual write: the document and its job commit or roll back together.
- Jobs survive a restart of any process; their state is queryable with SQL and visible
  in the admin UI.
- One less moving part in the core: Redis stays a cache and rate limiter, and its
  outage cannot lose indexing work.

Negative:

- Polling adds small, constant query load on PostgreSQL.
- `SKIP LOCKED` queues scale to hundreds of jobs per second, not tens of thousands.
  This is far above the project's needs.
- Lease, heartbeat, fencing and reaper are our code and need their own tests
  (crash during processing, expired lease, double claim).

## Alternatives considered

- **Redis list or Redis Streams (rejected).** Fast and simple to consume, but enqueueing
  is a second write next to the PostgreSQL commit. A crash between the two writes loses
  the job or creates a job for a document that does not exist. Fixing that needs an
  outbox table anyway. Redis persistence settings also decide whether jobs survive a
  restart.
- **Kafka (rejected for this queue).** Kafka is used for domain events through a
  transactional outbox (ADR-010) in an optional Compose profile. Making indexing depend
  on it would make the core depend on that profile. Per-job leases, retries with
  backoff and a dead state are also easier to model as table rows than as log offsets.
- **Celery or another task framework (rejected).** It still needs a broker (Redis or
  RabbitMQ), so it has the same dual-write problem, and it hides the lease and retry
  logic that this project wants to show explicitly.
