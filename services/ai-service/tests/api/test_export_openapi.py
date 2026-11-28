"""Tests for the FastAPI schema export used by the drift check."""

import json
from pathlib import Path

import pytest

from ai_service.api.export_openapi import build_schema, main


def test_schema_is_openapi_31_with_health_paths() -> None:
    schema = build_schema()
    assert schema["openapi"].startswith("3.1")
    assert {"/internal/health/live", "/internal/health/ready"} <= set(schema["paths"])


def test_main_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "schema.json"
    main(["export_openapi", str(out)])
    assert json.loads(out.read_text(encoding="utf-8")) == build_schema()


def test_main_prints_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    main(["export_openapi"])
    assert json.loads(capsys.readouterr().out) == build_schema()
