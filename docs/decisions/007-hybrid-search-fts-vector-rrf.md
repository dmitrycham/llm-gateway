# ADR-007: Hybrid search (FTS + vector, RRF) in Phase 1

- Status: Accepted
- Date: 2026-11-24

## Context

The RAG service must find the right fragments for a question within one workspace.
Vector search finds fragments with similar meaning, but it is weak on exact tokens:
product codes, article numbers, names, error codes and rare terms. These are common
in the questions that site visitors ask. Full-text search (FTS) is strong on exact
words and weak on paraphrases.

The evals (spec §14.2) measure Recall on a fixed set of questions. One missed
fragment changes Recall by about 3 percentage points, so retrieval quality is visible
in the results.

All data is in PostgreSQL already (pgvector for embeddings, ADR-002 for the queue).

## Decision

Retrieval in Phase 1 is hybrid: a vector channel and an FTS channel, merged with
Reciprocal Rank Fusion (RRF).

- **Vector channel.** pgvector, cosine distance, `top_k = 20`. The `embedding` column
  has no fixed dimension; each embedding profile has its own partial HNSW index on
  `embedding::vector(N)`. The query uses the same cast and a literal `profile_id` from
  trusted configuration, so the planner uses the partial index. Iterative index scans
  (pgvector ≥ 0.8, `hnsw.iterative_scan = relaxed_order`) keep small workspaces from
  losing rows to the post-index filter. With `relaxed_order`, pgvector may return
  candidates slightly out of distance order, so the order of the index output is not
  a rank. The query first materializes the candidates, then computes the rank as
  `row_number() OVER (ORDER BY distance + 0, chunk_id)`, and only this rank goes into
  RRF (`+ 0` keeps the planner from using the index order again; `chunk_id` breaks
  ties). Exact search without an index is allowed for small data; the mode
  (`exact` / `ann`) is a profile setting.
- **FTS channel.** `ai.chunks.tsv` is a generated column
  `to_tsvector(fts_config, content)` with a GIN index. The query uses
  `websearch_to_tsquery`, ranking uses `ts_rank_cd`, `top_k = 20`.
  `fts_config` comes from the document settings (default `russian`).
- **Both channels** filter by `workspace_id`, the active embedding profile and the
  active versions of documents that are not deleted.
- **Fusion.** Score of a fragment = `Σ 1 / (k + rank_i)` over both channels, `k = 60`.
  The best `context_top_n = 6` fragments go into the context, if they pass the
  profile's relevance threshold in the vector channel or have a non-zero FTS rank.
- The relevance threshold is set **per embedding profile**, because distance scales
  differ between models.

Default values live in spec §10.

## Consequences

Trade-off: better Recall on exact tokens (codes, names, numbers), in exchange for a
second index, a second query per question and more tuning parameters.

Positive:

- Questions with exact identifiers find their fragments even when embeddings miss them.
- RRF uses ranks, not raw scores, so the two channels need no score calibration.
- Everything runs in one PostgreSQL query plan per channel, inside one transaction with
  the RLS setting (ADR-008). No extra service.

Negative:

- More storage and write work: a GIN index on `tsv` next to the HNSW indexes.
- Two queries per question add latency (milliseconds; small next to model latency).
- The vector rank must be computed after a re-sort (see Decision). A test with
  out-of-order candidates checks that a closer fragment always gets the better rank.
- More parameters to tune: `top_k` of each channel, `k`, `context_top_n`, thresholds.
  They are fixed before an eval run and not changed during it.
- FTS quality depends on the language configuration. Mixed-language documents need a
  sensible `fts_config` choice.

## Alternatives considered

- **Vector search only (rejected).** Simpler, but it misses exact identifiers, which
  are a large part of real support questions.
- **Weighted sum of normalized scores (rejected).** Needs per-model score
  normalization and weights that drift when the embedding profile changes. RRF is
  stable without calibration.
- **External search engine such as OpenSearch (rejected).** Strong FTS, but a second
  data store to sync, secure and back up. PostgreSQL FTS is enough at this scale.
- **Cross-encoder reranker in Phase 1 (rejected for now).** It may improve precision,
  but it adds a model call per question. It can be added later if the evals show the
  need.
