# llm-gateway

AI Gateway in Go with an OpenAI-compatible API, plus an asynchronous RAG service
and an LLM evaluation suite. The RAG widget is the gateway's first client.

> Work in progress.

## Layout

| Path                   | What                                         |
|------------------------|----------------------------------------------|
| `api/openapi`          | OpenAPI contracts (public, internal, gateway) |
| `services/go-api`      | Public Go API                                |
| `services/gateway`     | Go AI Gateway (model calls, routing, budgets) |
| `services/ai-service`  | Python RAG service and worker                |
| `web`                  | Widget and web client                        |
| `infra`                | Compose, Helm, Terraform                     |
| `data`                 | Sample documents and eval sets               |
| `scripts`              | Helper scripts                               |
| `docs/decisions`       | Architecture Decision Records                |

## Development

Requirements: Go, golangci-lint v2, Python 3.13+, uv, Node.js with npm (for `npx`),
[oasdiff](https://github.com/oasdiff/oasdiff) 1.41+ (`brew install oasdiff`).

```sh
make lint     # golangci-lint + ruff + mypy + Redocly (OpenAPI)
make lint-api # Redocly lint of api/openapi contracts
make fmt      # format Go and Python code
make test-go
make test-py
```

### Contracts and generated code (ADR-005)

The YAML files in `api/openapi` are the source of truth. Change the contract first.

```sh
make generate           # regenerate Go code (go-api internal/aiclient) from the contracts
make check-generated    # fail if generated code is missing or stale (needs a clean tree)
make check-drift        # FastAPI schema vs internal-ai.yaml
make check-compat       # breaking changes vs BASE (default origin/main)
make check-compat-test  # tests of the breaking-change script
```

- `api/openapi/internal-ai.pending.txt` lists operations FastAPI does not implement yet;
  the drift check skips them. Delete a line in the PR that implements the operation.
- A breaking change needs a new path version. Before the first `v*` release tag, a PR
  labelled `contract-breaking-ok` may break a contract; CI ignores the label once a
  release tag exists.

## CI

| Workflow       | When                       | What                                                    |
|----------------|----------------------------|---------------------------------------------------------|
| `ci.yaml`      | PR, push to `main`         | lint, tests, generated code, drift, contract-compat     |
| `smoke.yml`    | PR, push to `main`         | Compose stack smoke test                                |
| `release.yaml` | push to `main`, `v*` tags  | build images, Trivy, push to GHCR                       |

- Required checks on `main` (strict, branch must be up to date): every `ci.yaml` job and
  `Compose smoke test`.
- Images: `ghcr.io/dmitrycham/llm-gateway-<service>`. `main` publishes `sha-<short>` and
  `main`; a `v1.2.3` tag publishes `1.2.3`. Each image is built once, scanned by Trivy
  (fails on fixable HIGH/CRITICAL), and only that scanned image is pushed. Reports go to
  Security → Code scanning and to the run's artifacts.

## Local stack

Requirements: Docker with Compose v2 (OrbStack or Docker Desktop on macOS).

```sh
cp .env.example .env          # then change the passwords
docker compose up --build -d
make smoke                    # scripts/smoke-test.sh
```

| Service      | Host address                        | Notes                                    |
|--------------|-------------------------------------|------------------------------------------|
| `go-api`     | `http://127.0.0.1:8090/health/live` | Public API                               |
| `postgres`   | `127.0.0.1:5432`                    | PostgreSQL 18 + pgvector                 |
| `gateway`    | not published                       | Health probes only; `:8080` in Stage 2   |
| `ai-service` | not published                       | Internal API (`internal-ai.yaml`)        |
| `redis`      | not published                       | Rate limits and cache                    |

- `migrate` runs once before the services start. It applies the `app`, `ai` and
  `gateway` migrations in that order as the `migrator` role, the owner of every object.
  Migrations live in `services/<service>/migrations` as
  `NNNNNN_<name>.up.sql` / `.down.sql` (golang-migrate). Schema-qualify every name.
- Ollama is optional and runs on the host (Docker on a Mac has no Metal GPU). Containers
  reach it at `http://host.docker.internal:11434`.
- `docker compose down` keeps the data. `docker compose down -v` is **destructive**: it
  deletes the database volume. Roles and their passwords are created only on the first
  start of an empty volume.
- The `events` and `observability` profiles are reserved for later stages.

## License

[MIT](LICENSE)
