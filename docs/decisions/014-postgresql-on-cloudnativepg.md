# ADR-014: PostgreSQL in Kubernetes with CloudNativePG and the Barman Cloud Plugin

- Status: Accepted
- Date: 2026-11-26

## Context

In Phases 2 and 3 PostgreSQL runs inside k3s (ADR-012). It holds everything that
matters: documents and chunks, sessions, idempotency keys, generation results
(ADR-009), budgets and the outboxes (ADR-010).

Phase 2 must show two demos (spec §13): restore the database to a point in time
before a document was deleted, and fail over to a replica under load while counting
lost transactions and the seconds without writes. Both need a replica, a continuous
WAL archive and base backups in object storage. Writing this with a StatefulSet and
scripts means writing failover, fencing and restore logic ourselves.

The database also needs the `pgvector` extension (spec §8.2) and the roles of
spec §8.4 and ADR-008.

## Decision

PostgreSQL in Kubernetes is managed by the CloudNativePG (CNPG) operator. Backups and
WAL archiving use the Barman Cloud Plugin.

- **Cluster.** One CNPG `Cluster` with two instances, a primary and an asynchronous
  replica. The application connects through the read-write service, which follows the
  primary after a failover.
- **Backups.** An `ObjectStore` resource of the plugin describes the bucket. The
  `Cluster` lists the plugin under `plugins` with `isWALArchiver: true`, so WAL is
  archived continuously. A `ScheduledBackup` with `method: plugin` takes base
  backups. The plugin replaces the operator's older built-in `barmanObjectStore`
  settings, so that path is not used.
- **Storage target.** Phase 2: local S3-compatible storage (the same service as
  ADR-015, a separate bucket). Phase 3: Amazon S3 (ADR-012).
- **Recovery** is never in place. A new `Cluster` bootstraps with `recovery` from an
  `externalClusters` entry that points at the `ObjectStore`, with
  `recoveryTarget.targetTime` for PITR (the time always with an explicit time zone).
  The document recovery demo restores into a separate cluster and pairs it with the
  object version of the same moment (ADR-015).
- **Retention.** The backup retention window is the PITR window. ADR-015 keeps old
  object versions at least as long.
- **Image.** The operand image must contain `pgvector`. The image and its PostgreSQL
  major are pinned in the chart; the check that the extension is present is a step in
  the Phase 2 runbook.
- **Migrations** run as a Kubernetes Job in the deploy pipeline with the `migrator`
  role, as in Compose. `postgres-exporter` metrics go to Prometheus (ADR-011).
- **PgBouncer** (CNPG `Pooler`) is added only if the k6 load test shows that
  connections are the bottleneck.

## Consequences

Trade-off: replica, PITR and automated failover without our own scripts, in exchange
for a dependency on the operator, its CRDs and the plugin, whose upgrades we must
follow.

Positive:

- Failover, replica rebuild and WAL archiving are done by a widely used operator,
  not by project code.
- Recovery is declarative: a restore is a manifest with a target time and can be
  repeated from the runbook.
- The same manifests work against local storage and Amazon S3; only the
  `ObjectStore` changes.

Negative:

- The replica is asynchronous: a failover can lose the last committed transactions.
  The failover demo measures this, and budget totals are reconciled with attempts
  afterwards.
- On a single-node k3s both instances share one host, so the replica protects against
  a PostgreSQL failure, not a host failure.
- Operator and plugin versions must be compatible, and CRD upgrades are a deployment
  step of their own.
- Recovery creates a new cluster with a new service name; switching the application to
  it is a documented manual step.

## Alternatives considered

- **StatefulSet with our own scripts (rejected).** Failover, fencing of the old
  primary and WAL archiving are the hard parts, and getting them wrong loses data.
  They are not what this project is meant to build.
- **Other operators, Zalando Postgres Operator or Crunchy PGO (rejected).** Both are
  capable. CNPG talks to the Kubernetes API directly without Patroni, has
  declarative recovery and is the operator named in the spec. One operator is enough.
- **PostgreSQL outside the cluster on the VM (rejected).** Simpler, but it skips
  exactly the in-cluster operation that Phase 2 is meant to practise, and backups
  would again be our scripts.
- **Amazon RDS (rejected as the default).** Managed backups and failover, but billed
  by the hour and AWS-only, so Phase 2 could not use it. It remains an optional short
  run in Phase 3 (ADR-012).
- **The operator's built-in `barmanObjectStore` backups (rejected).** Replaced by the
  Barman Cloud Plugin; new setups use the plugin.
