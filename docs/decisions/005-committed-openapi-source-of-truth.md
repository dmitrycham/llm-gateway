# ADR-005: Committed OpenAPI files are the source of truth

- Status: Accepted
- Date: 2026-11-24

## Context

The system has three HTTP APIs and services in two languages:

- the public and admin API of the Go API (widget, sessions, documents);
- the internal API of the Python RAG service, called by the Go API (ADR-001);
- the gateway API, called by external clients and by Python (ADR-003, ADR-013).

Go and Python must agree on every request, response and error. FastAPI builds an
OpenAPI schema from Python code, while Go has no such built-in export. If the schema
lives only in code, a change in one service can silently break the other, and nobody
reviews the contract as a contract.

## Decision

The committed OpenAPI 3.1 files in `api/openapi/` are the source of truth:

- `public.yaml`: public and admin API of the Go API;
- `internal-ai.yaml`: Go API → Python;
- `gateway.yaml`: the gateway API for external clients and internal callers.

Rules:

- An endpoint is added to the YAML file first, reviewed in the pull request, and only
  then implemented.
- **Go** generates server interfaces, models and clients from these files with
  `oapi-codegen` (it supports OpenAPI 3.0 and 3.1). Generated code is committed and
  CI checks that it is up to date.
- **Python** keeps FastAPI and Pydantic models in code. CI exports the FastAPI schema
  and compares it with `internal-ai.yaml` using `oasdiff`. Any difference blocks the
  merge, not only breaking ones.
- Python's client for the gateway (ADR-003) is checked against `gateway.yaml` by
  contract tests.
- Contract tests (for example `schemathesis`) run against the running services in CI.
- A breaking change needs a new path version (`/internal/v2/...`, `/api/v2/...`).
  `oasdiff` breaking-change detection guards the published version.
- The shared error contract (spec §7.4), status codes and examples are part of the
  YAML files.

## Consequences

Trade-off: the contract is reviewed and versioned on its own, in exchange for keeping
the YAML file and the FastAPI code in sync by hand.

Positive:

- One reviewed contract per API. A reviewer sees API changes as a YAML diff.
- Go code cannot drift from the contract: it is generated.
- Python drift is caught in CI before merge, not in production.
- The same tool chain (OpenAPI, `oapi-codegen`, `oasdiff`, contract tests) works for
  all three APIs.

Negative:

- Every Python API change is made twice: in the YAML file and in Pydantic models.
- The FastAPI export and a hand-written YAML file differ in harmless details
  (schema names, `title`, ordering). The comparison must normalize them, or the check
  becomes noisy and people start to ignore it.
- Generated Go code adds lines to pull requests.

## Alternatives considered

- **Code-first: the FastAPI export is the contract (rejected).** Easy for Python, but
  the Go side would follow whatever Python produced, and the gateway and public API
  have no Python code to export from.
- **Generate Python models from the YAML files (rejected).** It removes the double edit,
  but generated Pydantic code is harder to read and to extend with validators. The CI
  comparison gives the same safety with hand-written models.
- **Protobuf and gRPC as the contract (rejected).** It would add a second contract
  system next to OpenAPI. See ADR-001.
