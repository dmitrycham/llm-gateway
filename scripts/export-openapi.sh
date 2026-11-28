#!/usr/bin/env bash
# Export the FastAPI OpenAPI schema of ai-service as JSON (ADR-005 drift check).
#
# Usage: scripts/export-openapi.sh [output-path]   (no path: stdout)
set -euo pipefail

out="${1:-}"
# Absolute before the cd below, so a relative path keeps meaning the caller's directory.
if [[ -n "$out" && "$out" != /* ]]; then
  out="$PWD/$out"
fi

cd "$(dirname "$0")/../services/ai-service"
if [[ -n "$out" ]]; then
  uv run --locked --quiet python -m ai_service.api.export_openapi "$out"
else
  uv run --locked --quiet python -m ai_service.api.export_openapi
fi
