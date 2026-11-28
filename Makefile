GO_MODULES := services/go-api services/gateway
PY_SERVICE := services/ai-service
GOLANGCI   := golangci-lint
UV         := uv
# Absolute path, because golangci-lint runs inside each module directory.
GOLANGCI_CONFIG := $(CURDIR)/.golangci.yml
# Pinned so local runs and CI lint the contracts with the same rules.
REDOCLY    := npx --yes @redocly/cli@2.83.0
OPENAPI    := api/openapi/internal-ai.yaml api/openapi/gateway.yaml api/openapi/public.yaml
GO_API     := services/go-api
AICLIENT   := $(GO_API)/internal/aiclient/aiclient.gen.go
# Base commit for the breaking-change check; CI passes the PR merge base explicitly.
BASE       ?= origin/main

.PHONY: help lint lint-go lint-py lint-api fmt fmt-go fmt-py test test-go test-py py-sync smoke \
	generate check-generated check-drift check-compat check-compat-test

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

lint: lint-go lint-py lint-api ## Run all linters

lint-api: ## Lint OpenAPI contracts
	$(REDOCLY) lint --config redocly.yaml $(OPENAPI)

# golangci-lint does not lint a go.work multi-module tree with ./..., so run it per module.
lint-go:
	@for m in $(GO_MODULES); do \
		echo "==> golangci-lint $$m"; \
		(cd $$m && $(GOLANGCI) run --config "$(GOLANGCI_CONFIG)" ./...) || exit 1; \
	done

lint-py: py-sync
	cd $(PY_SERVICE) && $(UV) run ruff check .
	cd $(PY_SERVICE) && $(UV) run ruff format --check .
	cd $(PY_SERVICE) && $(UV) run mypy

fmt: fmt-go fmt-py ## Format Go and Python code

fmt-go:
	@for m in $(GO_MODULES); do \
		(cd $$m && $(GOLANGCI) fmt --config "$(GOLANGCI_CONFIG)" ./...) || exit 1; \
	done

fmt-py: py-sync
	cd $(PY_SERVICE) && $(UV) run ruff check --fix .
	cd $(PY_SERVICE) && $(UV) run ruff format .

test: test-go test-py ## Run all tests

test-go: ## Run Go tests
	@for m in $(GO_MODULES); do \
		echo "==> go test $$m"; \
		(cd $$m && go test -race ./...) || exit 1; \
	done

test-py: py-sync ## Run Python tests
	cd $(PY_SERVICE) && $(UV) run pytest

py-sync:
	@cd $(PY_SERVICE) && $(UV) sync --locked --quiet

smoke: ## Smoke-test the running Compose stack
	scripts/smoke-test.sh

generate: ## Regenerate Go code from the OpenAPI contracts
	cd $(GO_API) && go generate ./...

# Needs a clean go-api tree: any modified or untracked file after `generate` fails the check.
check-generated: ## Fail if generated Go code is missing or out of date (uses git)
	@git ls-files --error-unmatch -- $(AICLIENT) >/dev/null 2>&1 || \
		{ echo "$(AICLIENT) is not committed: run make generate and commit it" >&2; exit 1; }
	$(MAKE) generate
	@if [ -n "$$(git status --porcelain -- $(GO_API))" ]; then \
		echo "Generated code is out of date: run make generate and commit the result." >&2; \
		git status --porcelain -- $(GO_API) >&2; \
		git --no-pager diff --stat -- $(GO_API) >&2; \
		exit 1; \
	fi
	cd $(GO_API) && go build ./...

check-drift: ## Compare the FastAPI schema with internal-ai.yaml (needs oasdiff)
	scripts/check-openapi-drift.sh

check-compat: ## Breaking-change check of the contracts against BASE (needs oasdiff, git)
	scripts/check-openapi-compat.sh $(BASE)

check-compat-test: ## Test the breaking-change check script (needs oasdiff, git)
	scripts/check-openapi-compat.test.sh
