# go-api

Public Go API. See the root README.

## Run

From `services/go-api`:

```sh
go run ./cmd/go-api
curl -i http://127.0.0.1:8090/health/live
```

The HTTP API follows [`public.yaml`](../../api/openapi/public.yaml).
`GET /health/live` is the liveness probe (spec §7.1): `200 {"status":"ok"}` while the
process serves HTTP. `/health/ready` comes with the first dependency (the database pool).

Every response carries `X-Request-ID`, a new UUIDv7. The Go API is the entry point, so it
always creates the ID and ignores any client value (spec §4.2, ADR-001). The ID is stored
in the request context and added as `request_id` to every log line written with that
context. Logs are JSON on stdout, one access-log line per request (`aborted: true` when a
response was cut off).

The whole request, body included, must arrive within 15 seconds (`ReadTimeout`).

On SIGTERM or Ctrl-C the listener closes at once, so new connections are refused, and
in-flight requests get `GO_API_SHUTDOWN_TIMEOUT` to finish. After that the remaining
connections are closed and the process exits with code 1. A second signal kills the
process at once.

Settings come from environment variables only:

| Variable                  | Default | Meaning                                               |
|---------------------------|---------|-------------------------------------------------------|
| `GO_API_HTTP_ADDR`        | `:8090` | Listen address                                        |
| `GO_API_SHUTDOWN_TIMEOUT` | `15s`   | Wait for in-flight requests on shutdown (Go duration) |
| `GO_API_LOG_LEVEL`        | `info`  | `debug`, `info`, `warn` or `error`                    |

An invalid value stops the service at startup with an error that names the variable.
