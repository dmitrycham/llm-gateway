# ADR-008: Row-Level Security on the `ai.*` search tables

- Status: Accepted
- Date: 2026-11-24

## Context

All workspaces share the same tables. Every search and indexing query must filter by
`workspace_id`. One forgotten filter in a query leaks another tenant's documents into
a RAG context, and from there into an answer. This is the worst failure the product can
have, and code review alone does not reliably catch it.

The search queries are hand-written SQL (per-profile casts, partial indexes, RRF;
ADR-007), so an ORM-level tenant filter does not help.

## Decision

Row-Level Security (RLS) is enabled on the search tables as a second line of defence:
`ai.documents`, `ai.document_versions`, `ai.chunks`, `ai.chunk_embeddings`.

```sql
ALTER TABLE ai.chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai.chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON ai.chunks
  USING (workspace_id = current_setting('app.workspace_id')::uuid)
  WITH CHECK (workspace_id = current_setting('app.workspace_id')::uuid);
```

Rules:

- Every transaction of the AI service and the worker sets the workspace first:
  `SELECT set_config('app.workspace_id', $1, true)` (the same as `SET LOCAL`, but it
  accepts a bind parameter). The setting ends with the transaction, so a pooled
  connection never carries a workspace into the next request.
- Without the setting, the policy fails: `current_setting` raises an error when the
  parameter was never set, and `''::uuid` raises an error when it was reset.
  No rows are returned silently. This is fail-closed.
- `FORCE ROW LEVEL SECURITY` applies the policy to the table owner too. The
  application role is not a superuser and has no `BYPASSRLS`. Migrations run as a
  separate role.
- The explicit `workspace_id` filter stays in every query. Indexes need it, and RLS is
  a safety net, not the main filter.
- The worker sets `app.workspace_id` from the job before it touches documents.
  `ai.ingestion_jobs` is not under RLS: the worker must claim jobs from all workspaces.
- Negative tests run queries with workspace A set and check that rows of workspace B
  are never returned or written.

## Consequences

Trade-off: protection from a forgotten `workspace_id` filter, in exchange for a
mandatory per-transaction setting and a little query overhead.

Positive:

- A missing filter in a new query returns no foreign rows; it does not leak data.
- `WITH CHECK` also stops writes of rows into the wrong workspace.
- Fail-closed: a missing setting is an error that tests catch, not an empty or full
  result.

Negative:

- Every read, even a single-row lookup, must run in a transaction with the setting.
  Helpers in Python must make this the only way to get a connection.
- The policy adds a condition to every query. With the explicit filter and indexes
  the cost is small, but plans must be checked with `EXPLAIN ANALYZE`.
- Cross-workspace maintenance (purge, reaper, admin reports) needs a separate role
  or an explicit design. It cannot just run as the application role.
- Plain `SET` (without `LOCAL`) would leak the setting through the connection pool.
  Code review and a lint rule must forbid it.

## Alternatives considered

- **Application filter only (rejected).** It works until one query forgets it. The
  failure is silent and the impact is a cross-tenant data leak.
- **Schema or database per workspace (rejected).** Strong isolation, but migrations,
  HNSW indexes per profile and connection pools multiply by the number of workspaces.
  Too heavy for this project.
- **Session-level `SET app.workspace_id` (rejected).** With a connection pool the
  value survives the request and the next request on that connection sees the wrong
  workspace.
