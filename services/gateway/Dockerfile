# syntax=docker/dockerfile:1
FROM golang:1.27.4-alpine3.24 AS build
WORKDIR /src
# go.sum* matches nothing while the module has no dependencies.
COPY go.mod go.sum* ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway

# Alpine instead of distroless: busybox wget serves the Compose healthcheck.
# Kubernetes probes use httpGet and do not need it.
FROM alpine:3.24.4
RUN adduser -D -H -u 10001 app
COPY --from=build /out/gateway /usr/local/bin/gateway
USER 10001
EXPOSE 8081
ENTRYPOINT ["/usr/local/bin/gateway"]
