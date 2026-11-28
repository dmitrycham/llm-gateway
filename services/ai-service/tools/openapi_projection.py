"""OpenAPI drift check between internal-ai.yaml and the FastAPI export (ADR-005).

Both documents are projected to what the API means for a caller, then compared with
``oasdiff diff --fail-on-diff``:

- only operations are kept; the contract also drops the operations in the pending list
  (not implemented yet), the export keeps all of them, so an unexpected route is a diff;
- path-level parameters are merged into each operation (operation wins on ``(name, in)``);
- each operation carries its effective security (root ``security`` unless overridden);
- every local ``$ref`` is inlined, so schema names and component layout never matter;
- ``components`` keeps only the security schemes named by an operation, because security
  requirements refer to schemes by name, not by ``$ref``.

Usage::

    python -m tools.openapi_projection check CONTRACT EXPORT [--pending FILE]
    python -m tools.openapi_projection project SPEC OUT [--pending FILE]
"""

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

# Wording and examples are reviewed in the YAML, not enforced against Python docstrings.
OASDIFF_EXCLUDE = "description,examples,title,summary"

Operation = tuple[str, str]
Spec = dict[str, Any]


class ProjectionError(Exception):
    """The document cannot be projected; the drift check must fail."""


def load_spec(path: Path) -> Spec:
    # JSON is valid YAML, so one loader reads the contract and the export.
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        msg = f"{path}: not an OpenAPI document"
        raise ProjectionError(msg)
    return data


def read_pending(path: Path) -> frozenset[Operation]:
    """Read ``METHOD /path`` lines; blank lines and ``#`` comments are ignored."""
    pending: set[Operation] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or parts[0].lower() not in HTTP_METHODS or not parts[1].startswith("/"):
            msg = f"{path}:{number}: expected 'METHOD /path', got {raw!r}"
            raise ProjectionError(msg)
        pending.add((parts[0].upper(), parts[1]))
    return frozenset(pending)


def _pointer(spec: Spec, ref: str) -> Any:  # noqa: ANN401 - any JSON value
    if not ref.startswith("#/"):
        msg = f"only local $ref values are supported, got {ref!r}"
        raise ProjectionError(msg)
    node: Any = spec
    for token in ref[2:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            msg = f"unresolved $ref {ref!r}"
            raise ProjectionError(msg)
        node = node[key]
    return node


def _inline(spec: Spec, node: Any, stack: tuple[str, ...] = ()) -> Any:  # noqa: ANN401
    if isinstance(node, list):
        return [_inline(spec, item, stack) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref in stack:
            # Inlining a recursive schema never ends; such a schema needs a different check.
            msg = f"recursive $ref {ref!r} cannot be inlined"
            raise ProjectionError(msg)
        target = _pointer(spec, ref)
        resolved = _inline(spec, target, (*stack, ref))
        # OpenAPI 3.1 allows keywords next to $ref (for example a description); they apply on
        # top of the target.
        siblings = {k: _inline(spec, v, stack) for k, v in node.items() if k != "$ref"}
        if not siblings:
            return resolved
        if not isinstance(resolved, dict):
            msg = f"$ref {ref!r} with sibling keywords must point to an object"
            raise ProjectionError(msg)
        return {**resolved, **siblings}
    return {key: _inline(spec, value, stack) for key, value in node.items()}


def _merge_parameters(path_level: Iterable[Any], operation: Iterable[Any]) -> list[Any]:
    merged: dict[tuple[str, str], Any] = {}
    for param in [*path_level, *operation]:
        if not isinstance(param, Mapping) or "name" not in param or "in" not in param:
            msg = f"parameter without name/in: {param!r}"
            raise ProjectionError(msg)
        merged[(param["name"], param["in"])] = param
    return list(merged.values())


def project(spec: Spec, pending: frozenset[Operation] = frozenset()) -> Spec:
    root_security = spec.get("security")
    schemes = spec.get("components", {}).get("securitySchemes", {})
    seen_pending: set[Operation] = set()
    used_schemes: set[str] = set()
    paths: dict[str, dict[str, Any]] = {}

    for path, raw_item in spec.get("paths", {}).items():
        item = _inline(spec, raw_item)
        path_params = item.get("parameters", [])
        for method, raw_op in item.items():
            if method not in HTTP_METHODS:
                continue
            key = (method.upper(), path)
            if key in pending:
                seen_pending.add(key)
                continue
            op = copy.deepcopy(raw_op)
            params = _merge_parameters(path_params, op.get("parameters", []))
            if params:
                op["parameters"] = params
            else:
                op.pop("parameters", None)
            security = op.get("security", root_security)
            # No requirement and an empty list both mean "no authentication".
            if security:
                op["security"] = security
                for requirement in security:
                    used_schemes.update(requirement)
            else:
                op.pop("security", None)
            paths.setdefault(path, {})[method] = op

    stale = pending - seen_pending
    if stale:
        names = ", ".join(f"{m} {p}" for m, p in sorted(stale))
        msg = f"pending operations not in the contract (remove them from the list): {names}"
        raise ProjectionError(msg)

    missing = used_schemes - schemes.keys()
    if missing:
        msg = f"security schemes not defined in components: {', '.join(sorted(missing))}"
        raise ProjectionError(msg)

    projected: Spec = {
        "openapi": spec.get("openapi"),
        # Identical placeholder on both sides: info is metadata, not behaviour.
        "info": {"title": "projection", "version": "0"},
        "paths": paths,
    }
    if used_schemes:
        projected["components"] = {
            "securitySchemes": {name: _inline(spec, schemes[name]) for name in sorted(used_schemes)}
        }
    return projected


def write_spec(spec: Spec, path: Path) -> None:
    path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def oasdiff_diff(base: Path, revision: Path) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("oasdiff")
    if binary is None:
        msg = "oasdiff not found on PATH (see README: CI checks)"
        raise ProjectionError(msg)
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [
            binary,
            "diff",
            str(base),
            str(revision),
            "--fail-on-diff",
            "--exclude-elements",
            OASDIFF_EXCLUDE,
            "--format",
            "text",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def check(contract: Spec, export: Spec, pending: frozenset[Operation]) -> tuple[bool, str]:
    """Return ``(no_drift, oasdiff output)``."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "contract.json"
        revision = Path(tmp) / "export.json"
        write_spec(project(contract, pending), base)
        write_spec(project(export), revision)
        result = oasdiff_diff(base, revision)
    output = (result.stdout + result.stderr).strip()
    if result.returncode not in (0, 1):
        msg = f"oasdiff failed with exit code {result.returncode}: {output}"
        raise ProjectionError(msg)
    return result.returncode == 0, output


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    check_cmd = sub.add_parser("check", help="compare the contract with the FastAPI export")
    check_cmd.add_argument("contract", type=Path)
    check_cmd.add_argument("export", type=Path)
    check_cmd.add_argument("--pending", type=Path)
    project_cmd = sub.add_parser("project", help="write one projected document")
    project_cmd.add_argument("spec", type=Path)
    project_cmd.add_argument("out", type=Path)
    project_cmd.add_argument("--pending", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        pending = read_pending(args.pending) if args.pending else frozenset()
        if args.command == "project":
            write_spec(project(load_spec(args.spec), pending), args.out)
            return 0
        ok, output = check(load_spec(args.contract), load_spec(args.export), pending)
    except ProjectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if ok:
        print("No drift between the contract and the FastAPI export.")
        return 0
    print("Drift between the contract and the FastAPI export:", file=sys.stderr)
    print(output, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
