# ADR-011: Phase 1 observability with OpenTelemetry, Prometheus and Grafana

- Status: Accepted
- Date: 2026-11-26

## Context

A widget answer crosses several processes: Go API → Python → Go Gateway → provider,
and indexing runs in the worker. Events go through Kafka (ADR-010). Logs alone do not
show where the time of one request went, why a provider failed, or how close a
workspace is to its budget.

FR-06 asks for traces from Go to Python, the worker and Kafka, and for Prometheus
metrics on errors, calls, retries, refusals, circuit breaker trips, budget
reservations, tokens and latency. The Definition of Done asks for dashboards from the
repository and one request's trace visible from Go to the worker.

At the same time, the base stack (`docker compose up --build`) should stay small and
must not depend on monitoring containers.

## Decision

Observability is part of Phase 1 and runs in the Compose profile `observability`.

- **Instrumentation is always in the code.** Go and Python initialise the
  OpenTelemetry SDK (Stage 1). Without the profile the exporter is off and
  instrumentation is a no-op. A missing collector never fails a request or start-up.
- **Traces.** OTLP from all services to the OpenTelemetry Collector. Context is
  propagated with W3C `traceparent` over HTTP and in Kafka message headers (ADR-010).
  `request_id` is a span attribute on every span, so a trace can be found from an
  error body.
- **Context through PostgreSQL.** Work that crosses a table keeps its context in the
  row: `ai.ingestion_jobs` and the outbox rows (ADR-010) store `traceparent` and
  `request_id` from the transaction that created them. The worker and the relays
  start their spans from the stored context and log the stored `request_id`, so an
  indexing run joins the upload trace even after a worker restart. These columns are
  additions to spec §8.1.
- **Trace store.** The Collector exports traces to Grafana Tempo (single binary,
  local storage), and Grafana reads them. The spec lists three containers for the
  profile; a trace backend is needed for the "one trace from Go to the worker"
  criterion, so the profile has four: `otel-collector`, `tempo`, `prometheus`,
  `grafana`.
- **Metrics.** Each service exposes Prometheus `/metrics` on its internal listener
  only (for the gateway, `:8081`, never on the public `:8080`). Prometheus scrapes
  them. Metric names and labels are fixed in code; labels never contain user text,
  API keys or other high-cardinality values such as `request_id`.
- **Dashboards as code.** Grafana data sources and dashboards are provisioned from
  `infra/observability/`: latency p50/p95, time to first token, share of cloud calls,
  provider errors, circuit breaker state, budgets, indexing queue, outbox size and
  consumer lag (Stage 10).
- **Business data.** Usage and budget panels read PostgreSQL views through the
  `grafana_reader` role (SELECT on reporting views only, spec §8.4). The same data is
  also available through the usage report API.
- **Logs** stay structured JSON on stdout with `request_id`, `workspace_id`,
  `trace_id`, duration and status. A log store (Loki) is not part of Phase 1.
- In Phase 2 the same Collector, Prometheus, Tempo and Grafana configuration moves into
  the cluster (ADR-012).

## Consequences

Trade-off: latency, provider errors, budgets and consumer lag are visible, and one
request can be followed across services, in exchange for four extra containers and
instrumentation code to maintain. The base stack runs without them.

Positive:

- One trace shows where a slow answer spent its time, including the provider call and
  retries in the gateway.
- Errors reported by clients can be found by `request_id`.
- Dashboards are reviewed and versioned like code.

Negative:

- Without the profile there are no traces or dashboards, only logs. Debugging a
  cross-service problem needs the profile on.
- Logs are not correlated in Grafana: they are read with `docker compose logs` and
  matched by `trace_id`.
- Metric cardinality must be watched in review: a label with an unbounded value can
  make Prometheus use a lot of memory.
- Instrumentation libraries in two languages must be kept in step with the Collector.

## Alternatives considered

- **Logs only (rejected).** Cannot show where time went across three processes, and
  rates and percentiles would have to be computed from logs by hand.
- **Full LGTM stack with Loki and Mimir (rejected for Phase 1).** Correlated logs are
  useful, but Loki and Mimir add containers and storage settings that the project's
  volume does not need. Loki can be added later behind the same Collector.
- **Jaeger as the trace store (rejected).** Equally able to store traces, but a second
  UI next to Grafana. Tempo keeps traces, metrics and dashboards in one place.
- **A hosted observability service (rejected).** Sends traces with workspace metadata
  to a third party, needs an account and a bill, and breaks "the project runs
  locally".
- **Observability in the base stack (rejected).** Every developer and CI run would pay
  for four more containers that most runs do not need.
