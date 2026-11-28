#!/usr/bin/env bash
# Fail when the FastAPI schema of ai-service differs from api/openapi/internal-ai.yaml
# (ADR-005). Operations listed in api/openapi/internal-ai.pending.txt are not implemented
# yet and are skipped. The comparison rules live in services/ai-service/tools/openapi_projection.py.
#
# Needs: uv, oasdiff on PATH.
set -euo pipefail

cd "$(dirname "$0")/.."
root="$PWD"

# Explicit template: macOS mktemp ignores TMPDIR without one.
tmp="$(mktemp -d "${TMPDIR:-/tmp}/openapi-drift.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

scripts/export-openapi.sh "$tmp/fastapi.json"

cd services/ai-service
uv run --locked --quiet python -m tools.openapi_projection check \
  "$root/api/openapi/internal-ai.yaml" "$tmp/fastapi.json" \
  --pending "$root/api/openapi/internal-ai.pending.txt"
