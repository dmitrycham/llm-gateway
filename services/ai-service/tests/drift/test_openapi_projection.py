"""Tests for the OpenAPI drift check (tools/openapi_projection.py)."""

import copy
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from ai_service.api.export_openapi import build_schema
from tools.openapi_projection import (
    ProjectionError,
    check,
    load_spec,
    main,
    project,
    read_pending,
)

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "api/openapi/internal-ai.yaml"
PENDING = REPO / "api/openapi/internal-ai.pending.txt"

Spec = dict[str, Any]


@pytest.fixture
def needs_oasdiff() -> None:
    # Locally oasdiff is optional; in CI (CI=true) a missing binary must fail, not skip.
    if shutil.which("oasdiff") is None:
        if os.environ.get("CI"):
            pytest.fail("oasdiff is required in CI")
        pytest.skip("oasdiff not installed")


@pytest.fixture
def contract() -> Spec:
    return load_spec(CONTRACT)


@pytest.fixture
def export() -> Spec:
    return build_schema()


def _op(security: list[dict[str, list[str]]] | None = None) -> Spec:
    op: Spec = {"operationId": "probe", "responses": {"200": {"description": "ok"}}}
    if security is not None:
        op["security"] = security
    return op


def _spec(paths: Spec, **extra: Any) -> Spec:  # noqa: ANN401
    return {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": paths, **extra}


# --- drift check against oasdiff -------------------------------------------------------


@pytest.mark.usefixtures("needs_oasdiff")
def test_baseline_has_no_drift(contract: Spec, export: Spec) -> None:
    ok, output = check(contract, export, read_pending(PENDING))
    assert ok, output


@pytest.mark.usefixtures("needs_oasdiff")
def test_changed_response_field_is_drift(contract: Spec, export: Spec) -> None:
    schema = export["components"]["schemas"]["LivenessStatus"]
    schema["properties"]["uptime_s"] = {"type": "integer"}
    ok, output = check(contract, export, read_pending(PENDING))
    assert not ok
    assert "uptime_s" in output


@pytest.mark.usefixtures("needs_oasdiff")
def test_extra_fastapi_path_is_drift(contract: Spec, export: Spec) -> None:
    export["paths"]["/internal/debug"] = {"get": _op()}
    ok, output = check(contract, export, read_pending(PENDING))
    assert not ok
    assert "/internal/debug" in output


@pytest.mark.usefixtures("needs_oasdiff")
def test_contract_only_path_parameter_is_drift(contract: Spec, export: Spec) -> None:
    contract["paths"]["/internal/health/live"]["parameters"] = [
        {"name": "X-Probe-ID", "in": "header", "schema": {"type": "string"}},
    ]
    ok, output = check(contract, export, read_pending(PENDING))
    assert not ok
    assert "X-Probe-ID" in output


@pytest.mark.usefixtures("needs_oasdiff")
def test_security_scheme_type_change_is_drift() -> None:
    bearer = {"serviceToken": {"type": "http", "scheme": "bearer"}}
    api_key = {"serviceToken": {"type": "apiKey", "in": "header", "name": "X-Token"}}
    paths = {"/p": {"get": _op([{"serviceToken": []}])}}
    base = _spec(paths, components={"securitySchemes": bearer})
    revision = _spec(copy.deepcopy(paths), components={"securitySchemes": api_key})
    ok, _ = check(base, revision, frozenset())
    assert not ok


@pytest.mark.usefixtures("needs_oasdiff")
def test_main_check_exit_codes(tmp_path: Path, export: Spec) -> None:
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")
    args = ["check", str(CONTRACT), str(export_path), "--pending", str(PENDING)]
    assert main(args) == 0
    assert main(["check", str(CONTRACT), str(export_path)]) == 1


# --- projection rules ------------------------------------------------------------------


def test_pending_operation_is_removed_and_export_keeps_everything(
    contract: Spec, export: Spec
) -> None:
    projected = project(contract, read_pending(PENDING))
    assert "/internal/v1/chat/generate" not in projected["paths"]
    assert set(project(export)["paths"]) == set(export["paths"])


def test_stale_pending_entry_fails(contract: Spec) -> None:
    pending = read_pending(PENDING) | {("DELETE", "/internal/v1/gone")}
    with pytest.raises(ProjectionError, match="DELETE /internal/v1/gone"):
        project(contract, pending)


def test_read_pending_ignores_comments_and_rejects_bad_lines(tmp_path: Path) -> None:
    good = tmp_path / "good.txt"
    good.write_text("# header\n\npost /a  # trailing\nGET /b\n", encoding="utf-8")
    assert read_pending(good) == {("POST", "/a"), ("GET", "/b")}
    bad = tmp_path / "bad.txt"
    bad.write_text("FETCH /a\n", encoding="utf-8")
    with pytest.raises(ProjectionError, match=r"bad\.txt:1"):
        read_pending(bad)


def test_effective_security_and_named_schemes(contract: Spec) -> None:
    projected = project(contract, read_pending(PENDING))
    # Health overrides the root requirement with `security: []`: no authentication.
    assert "security" not in projected["paths"]["/internal/health/live"]["get"]
    assert "components" not in projected

    spec = _spec(
        {"/p": {"get": _op()}, "/open": {"get": _op([])}},
        security=[{"serviceToken": []}],
        components={"securitySchemes": {"serviceToken": {"type": "http", "scheme": "bearer"}}},
    )
    projected = project(spec)
    assert projected["paths"]["/p"]["get"]["security"] == [{"serviceToken": []}]
    assert "security" not in projected["paths"]["/open"]["get"]
    assert projected["components"] == {
        "securitySchemes": {"serviceToken": {"type": "http", "scheme": "bearer"}}
    }


def test_missing_named_scheme_fails() -> None:
    spec = _spec({"/p": {"get": _op([{"serviceToken": []}])}})
    with pytest.raises(ProjectionError, match="serviceToken"):
        project(spec)


def test_path_parameters_merge_with_operation_override() -> None:
    op = _op()
    op["parameters"] = [{"name": "X-A", "in": "header", "required": True}]
    spec = _spec(
        {
            "/p": {
                "parameters": [
                    {"name": "X-A", "in": "header", "required": False},
                    {"name": "X-B", "in": "header"},
                ],
                "get": op,
            }
        }
    )
    params = project(spec)["paths"]["/p"]["get"]["parameters"]
    assert {(p["name"], p.get("required")) for p in params} == {("X-A", True), ("X-B", None)}


def test_refs_are_inlined_with_sibling_keywords() -> None:
    op = _op()
    op["responses"]["200"] = {"$ref": "#/components/responses/Ok"}
    spec = _spec(
        {"/p": {"get": op}},
        components={
            "responses": {
                "Ok": {
                    "description": "ok",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Body", "description": "x"}
                        }
                    },
                }
            },
            "schemas": {"Body": {"type": "object"}},
        },
    )
    response = project(spec)["paths"]["/p"]["get"]["responses"]["200"]
    assert response["content"]["application/json"]["schema"] == {
        "type": "object",
        "description": "x",
    }


def test_recursive_ref_fails() -> None:
    op = _op()
    op["responses"]["200"] = {
        "description": "ok",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}},
    }
    node = {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Node"}}}
    spec = _spec({"/p": {"get": op}}, components={"schemas": {"Node": node}})
    with pytest.raises(ProjectionError, match="recursive"):
        project(spec)


def test_unresolved_and_external_refs_fail() -> None:
    for ref, message in (("#/components/schemas/Nope", "unresolved"), ("other.yaml#/X", "local")):
        op = _op()
        op["responses"]["200"] = {"$ref": ref}
        with pytest.raises(ProjectionError, match=message):
            project(_spec({"/p": {"get": op}}))
