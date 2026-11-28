#!/usr/bin/env bash
# Tests for scripts/check-openapi-compat.sh, one throwaway Git repository per case.
# Needs: git, oasdiff on PATH (or OASDIFF).
set -euo pipefail

script="$(cd "$(dirname "$0")" && pwd)/check-openapi-compat.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/openapi-compat-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

# The fixture repositories must not pick up the developer's Git config, hooks or signing.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid
export CONTRACTS="api.yaml"

failures=0

# A minimal contract; $1 is the `required` list of the response body.
contract() {
  cat <<YAML
openapi: 3.1.0
info: {title: t, version: '1', license: {name: MIT, identifier: MIT}}
paths:
  /items:
    get:
      operationId: listItems
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [$1]
                properties:
                  id: {type: string}
                  name: {type: string}
YAML
}

# new_repo <name>: a repository with an empty first commit, printed path.
new_repo() {
  local dir="$work/$1"
  mkdir -p "$dir"
  git -C "$dir" init --quiet
  git -C "$dir" commit --quiet --allow-empty -m base
  echo "$dir"
}

commit() { # dir, message
  git -C "$1" add -A
  git -C "$1" commit --quiet -m "$2"
}

# expect <name> <want: pass|fail> <want output substring or ""> <dir> <base> [env...]
expect() {
  local name="$1" want="$2" needle="$3" dir="$4" base="$5"
  shift 5
  local out rc=0
  out="$(cd "$dir" && env "$@" "$script" "$base" 2>&1)" || rc=$?
  local got=pass
  [[ $rc -eq 0 ]] || got=fail
  if [[ "$got" != "$want" ]]; then
    report "$name: want $want, got $got (exit $rc)" "$out"; return
  fi
  if [[ -n "$needle" && "$out" != *"$needle"* ]]; then
    report "$name: output lacks '$needle'" "$out"; return
  fi
  echo "ok    $name"
}

report() { # message, script output
  echo "FAIL  $1"
  while IFS= read -r line; do printf '      %s\n' "$line"; done <<< "$2"
  failures=$((failures + 1))
}

# 1. Contract first added at HEAD: skipped, not an error.
repo="$(new_repo new-contract)"
base="$(git -C "$repo" rev-parse HEAD)"
contract id > "$repo/api.yaml"; commit "$repo" add
expect "new contract is skipped" pass "skip  api.yaml" "$repo" "$base"

# 2. Unchanged contract passes.
repo="$(new_repo unchanged)"
contract id > "$repo/api.yaml"; commit "$repo" add
base="$(git -C "$repo" rev-parse HEAD)"
echo "# comment" >> "$repo/api.yaml"; commit "$repo" touch
expect "unchanged contract passes" pass "ok    api.yaml" "$repo" "$base"

# 3. Unknown base ref fails even with the bypass on.
expect "unknown base ref fails with bypass" fail "unknown base ref" "$repo" "no-such-ref" ALLOW_BREAKING=true

# 4. Malformed base contract fails even with the bypass on (oasdiff 102).
repo="$(new_repo malformed)"
printf 'openapi: 3.1.0\npaths: [\n' > "$repo/api.yaml"; commit "$repo" broken
base="$(git -C "$repo" rev-parse HEAD)"
contract id > "$repo/api.yaml"; commit "$repo" fix
expect "malformed base fails with bypass" fail "exited with 102" "$repo" "$base" ALLOW_BREAKING=true

# 5. Breaking change (required response field becomes optional): fails, bypass allows it.
repo="$(new_repo breaking)"
contract "id, name" > "$repo/api.yaml"; commit "$repo" add
base="$(git -C "$repo" rev-parse HEAD)"
contract id > "$repo/api.yaml"; commit "$repo" break
expect "breaking change fails" fail "breaking change in api.yaml" "$repo" "$base"
expect "breaking change allowed with bypass" pass "breaking change allowed: pre-release" "$repo" "$base" ALLOW_BREAKING=true

# 6. Contract deleted at HEAD fails.
repo="$(new_repo deleted)"
contract id > "$repo/api.yaml"; commit "$repo" add
base="$(git -C "$repo" rev-parse HEAD)"
rm "$repo/api.yaml"; commit "$repo" delete
expect "deleted contract fails" fail "was deleted" "$repo" "$base" ALLOW_BREAKING=true

if [[ $failures -gt 0 ]]; then
  echo "$failures case(s) failed" >&2
  exit 1
fi
echo "all cases passed"
