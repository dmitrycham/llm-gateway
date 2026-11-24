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

Requirements: Go, golangci-lint v2, Python 3.13+, uv.

```sh
make lint     # golangci-lint + ruff + mypy
make fmt      # format Go and Python code
make test-go
make test-py
```

## License

[MIT](LICENSE)
