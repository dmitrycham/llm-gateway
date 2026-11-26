# ai-service

Python RAG service and worker. See the root README.

## Run

From `services/ai-service`:

```sh
uv sync
uv run uvicorn ai_service.api.app:create_app --factory --host 127.0.0.1 --port 8000
curl -i http://127.0.0.1:8000/internal/health/live
```

The HTTP API follows [`internal-ai.yaml`](../../api/openapi/internal-ai.yaml). Every error,
including 404 and validation errors, uses its `ErrorResponse` format. Every response carries
`X-Request-ID`: the incoming value when it is a valid UUID or ULID, otherwise a new one.

Settings come from environment variables only (no `.env` file is read):

| Variable                         | Default    | Meaning                                       |
|----------------------------------|------------|-----------------------------------------------|
| `AI_SERVICE_POSTGRES_HOST`       | `postgres` | PostgreSQL host for the readiness check       |
| `AI_SERVICE_POSTGRES_PORT`       | `5432`     | PostgreSQL port                               |
| `AI_SERVICE_GATEWAY_HOST`        | `gateway`  | Go Gateway internal listener host             |
| `AI_SERVICE_GATEWAY_PORT`        | `8081`     | Go Gateway internal listener port             |
| `AI_SERVICE_REDIS_HOST`          | unset      | Redis host; unset turns off the Redis check   |
| `AI_SERVICE_REDIS_PORT`          | `6379`     | Redis port                                    |
| `AI_SERVICE_READINESS_TIMEOUT_S` | `1.0`      | Timeout of each readiness check, seconds      |

`/internal/health/ready` answers 503 until PostgreSQL and the gateway accept TCP
connections. For now the checks only open a TCP connection; they become real queries once
the database pool and the HTTP gateway client exist.
