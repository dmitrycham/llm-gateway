#!/usr/bin/env bash
# Fail on a breaking change to an OpenAPI contract compared with a base commit (ADR-005:
# a breaking change needs a new path version).
#
# Usage: scripts/check-openapi-compat.sh <base-ref>
# Runs in the Git repository of the current directory, so the test can use a fixture repo.
#
# Env:
#   ALLOW_BREAKING=true  accept ERR-level breaking changes (oasdiff exit 1) and keep checking.
#                        CI sets it only for a PR labelled contract-breaking-ok while no v*
#                        release tag exists. Any other failure still fails.
#   OASDIFF              oasdiff binary (default: oasdiff)
#   CONTRACTS            space-separated contract paths (default: the three in api/openapi)
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <base-ref>" >&2
  exit 2
fi

OASDIFF="${OASDIFF:-oasdiff}"
read -r -a contracts <<< "${CONTRACTS:-api/openapi/public.yaml api/openapi/internal-ai.yaml api/openapi/gateway.yaml}"

cd "$(git rev-parse --show-toplevel)"

if ! base_commit="$(git rev-parse --verify --quiet "${1}^{commit}")"; then
  echo "FAIL: unknown base ref: $1" >&2
  exit 1
fi

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/openapi-compat.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

for file in "${contracts[@]}"; do
  if ! git cat-file -e "${base_commit}:${file}" 2>/dev/null; then
    echo "skip  $file: new since ${base_commit:0:12}"
    continue
  fi
  if [[ ! -f "$file" ]]; then
    echo "FAIL: $file exists in ${base_commit:0:12} but was deleted" >&2
    exit 1
  fi

  base_spec="$tmpdir/$(basename "$file")"
  git show "${base_commit}:${file}" > "$base_spec"

  # oasdiff: 0 = no ERR-level change, 1 = ERR-level breaking change, other (102 = spec
  # cannot be loaded) = the check itself failed and must never be bypassed.
  rc=0
  "$OASDIFF" breaking --fail-on ERR "$base_spec" "$file" || rc=$?
  case "$rc" in
    0) echo "ok    $file" ;;
    1)
      if [[ "${ALLOW_BREAKING:-false}" != true ]]; then
        echo "FAIL: breaking change in $file (see above)" >&2
        exit 1
      fi
      echo "breaking change allowed: pre-release ($file)"
      ;;
    *)
      echo "FAIL: oasdiff exited with $rc for $file" >&2
      exit "$rc"
      ;;
  esac
done
