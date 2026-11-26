# ADR-010: Domain events through a transactional outbox to Kafka

- Status: Accepted
- Date: 2026-11-26

## Context

Some state changes are interesting outside the service that makes them: a finished chat
answer (`chat.message.completed`), an indexed or failed document version
(`kb.document.indexed`, `kb.document.failed`) and a recorded provider attempt
(`llm.usage.recorded`). Clients want signed webhooks for them, and the usage events
feed spending aggregates for reports and Evals (spec §4.6, Stage 7).

Publishing an event straight to a broker after the database commit is a dual write.
A crash between the two loses the event, and publishing before the commit can announce
a change that is then rolled back.

The core flows must not depend on the event path. Chat and indexing have to work when
the broker is down or not deployed at all. The indexing queue is already a PostgreSQL
table (ADR-002), and budgets are checked synchronously in PostgreSQL (ADR-003).

## Decision

Domain events are written to an outbox table in the same transaction as the business
change and are relayed to Kafka (KRaft mode).

- **One outbox per schema**, so schema ownership (spec §4.1) is kept: `app.outbox`
  (Go API), `ai.outbox` (Python) and `gateway.outbox` (Go Gateway). Columns:
  `id` (= `event_id`, UUIDv7), `workspace_id`, `event_type`, `aggregate_id`,
  `aggregate_version`, `payload` (jsonb), `traceparent`, `request_id`, `created_at`,
  `published_at`. `aggregate_version`, `traceparent` and `request_id` are additions to
  spec §8.1.
- **Writers.** Each event is written in the transaction of the state change it
  reports. The Go API writes `chat.message.completed`. Python writes
  `kb.document.indexed` in the version activation transaction (spec §8.2) and
  `kb.document.failed` in the transaction that moves the version to its terminal
  `failed` state, so every version ends with exactly one of the two. The Go Gateway
  writes `llm.usage.recorded` in the attempt accounting transaction, because it owns
  attempts and budgets (ADR-003, ADR-009). This replaces the v1 text of spec §4.6,
  where Python wrote the usage event and both `kb.document.*` events were written at
  activation.
- **Relay.** Each service relays only its own outbox, so no database role reads
  another schema (spec §8.4): `go-api` → `app.outbox`, `gateway` → `gateway.outbox`,
  `ai-worker` → `ai.outbox`. One relay instance per outbox is the leader (a session
  advisory lock); other instances wait. The leader reads rows with
  `published_at IS NULL ORDER BY id LIMIT 100`, publishes them with an idempotent
  producer and `acks=all`, then sets `published_at`.
- **Message format.** Key = `workspace_id`, so the events of one workspace share a
  partition. The payload holds IDs and metadata only, never message or document text,
  plus `aggregate_id`, `aggregate_version` and `occurred_at`. Headers carry
  `event_id`, `request_id` and `traceparent`, taken from the row, not from the relay's
  own context (ADR-011).
- **No ordering guarantee.** Events can arrive out of order, even within a workspace:
  a transaction with a lower `id` can commit after a later row was already published,
  and the three outboxes are relayed independently. `aggregate_version` grows with
  each change of its aggregate and is set in the same transaction as the event.
  `webhook-dispatcher` still creates one delivery per event and subscription,
  deduplicated by `(event_id, endpoint_id)`, so a late event is delivered, not
  dropped. Only a consumer that keeps a view of the latest state, or a receiver that
  wants only the latest state, skips an event whose version is not newer than the one
  it has applied for that aggregate. This replaces spec §4.6 step 4.
- **Delivery is at-least-once.** A crash between publishing and setting
  `published_at` produces a duplicate, so every consumer deduplicates by `event_id`.
- **Consumers.** `webhook-dispatcher` (Go) inserts `app.webhook_deliveries` with
  `ON CONFLICT (event_id, endpoint_id) DO NOTHING` and commits the Kafka offset only
  after the database commit. HTTP delivery runs from PostgreSQL (`SKIP LOCKED`), not
  from the consumer loop, so a slow endpoint does not block a partition. Requests are
  signed with HMAC-SHA256 and a timestamp. `usage-consumer` (Go) builds spending
  aggregates. A message that cannot be parsed goes to `events.dlq`.
- **Optional profile.** Kafka, `webhook-dispatcher` and `usage-consumer` run in the
  Compose profile `events`. With `EVENTS_ENABLED=false` no outbox rows are written.
  If Kafka is down while the profile is on, the outbox grows and the relay catches up
  later. Published rows and Kafka topics are kept for 7 days (spec §10).
- Kafka client libraries for Go and Python are chosen in Stage 7 after checking
  current versions. They must support an idempotent producer, `acks=all`, message
  headers and manual offset commits.

## Consequences

Trade-off: events are never lost or published for a rolled-back change, and clients
get webhooks, in exchange for one more container, a relay per service, and duplicates
and reordered events that every consumer must handle.

Positive:

- No dual write: the event commits or rolls back with the change it describes.
- Kafka being down or absent does not break chat, indexing or budgets.
- No extra locks in business transactions: event writes do not slow the budget path.
- The outbox doubles as an audit trail until cleanup.
- Traces continue across Kafka through the stored `traceparent`.

Negative:

- At-least-once only: consumers and webhook receivers must deduplicate by `event_id`.
- No ordering guarantee: receivers that keep the latest state must compare
  `aggregate_version`, and the webhook documentation must say so. One relay leader per outbox makes reordering rare but
  does not prevent it.
- Relays poll the database, and a stopped Kafka makes the outbox grow. Unpublished
  outbox size and consumer lag are metrics with alerts (ADR-011).
- Three services carry relay code, and Stage 7 needs failure tests: relay crash
  between publish and `published_at`, redelivery, a late event (v2, v1, duplicate v1
  → two delivery rows), a poison message, Kafka stopped and a run without the
  `events` profile.

## Alternatives considered

- **Publish to Kafka directly after the commit (rejected).** This is a dual write. A
  crash after the commit loses the event, and there is no table to replay from.
- **Strict order within a workspace (rejected).** It needs a sequence assigned under
  a workspace lock in every business transaction that writes an event, which makes
  all such writes of a workspace, budget settlement included, run one at a time.
  Ordering across `app`, `ai` and `gateway` would also need a sequence shared by three
  schemas, which breaks schema ownership.
- **Change data capture with Debezium (rejected).** It also avoids the dual write, but
  it adds Kafka Connect and logical replication slots to operate, and it couples the
  event format to table rows. A relay is a few dozen lines per service.
- **Indexing jobs and events in one Kafka pipeline (rejected).** ADR-002 keeps the
  indexing queue in PostgreSQL: the core must not depend on the `events` profile, and
  leases, retries and a dead state are simpler as rows.
- **Redis Streams or RabbitMQ instead of Kafka (rejected).** Either would work at this
  volume. Kafka is chosen for replayable retention, consumer groups and partition
  ordering, which the webhook and usage consumers use, and as a skill this project
  sets out to practise.
- **`LISTEN/NOTIFY` and webhooks sent from PostgreSQL only (rejected).** Notifications
  are lost when no listener is connected and they do not fan out to independent
  consumers.
