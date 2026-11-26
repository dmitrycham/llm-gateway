import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError

from ai_service.api import create_app, health
from ai_service.api.health import (
    Check,
    NotReadyChecks,
    ReadinessChecks,
    ReadyChecks,
    checks_from_settings,
    tcp_check,
)
from ai_service.api.settings import Settings

ClientFactory = Callable[[FastAPI], AsyncClient]


def constant(result: bool) -> Check:
    async def check() -> bool:
        return result

    return check


async def ready(app: FastAPI, make_client: ClientFactory) -> tuple[int, Any]:
    async with make_client(app) as client:
        response = await client.get("/internal/health/ready")
    return response.status_code, response.json()


async def test_live_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/internal/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_required_checks_pass(client: AsyncClient) -> None:
    response = await client.get("/internal/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "degraded": False,
        "checks": {"postgres": "ok", "gateway": "ok"},
    }


async def test_redis_down_keeps_service_ready_but_degraded(make_client: ClientFactory) -> None:
    checks = ReadinessChecks(postgres=constant(True), gateway=constant(True), redis=constant(False))

    status, body = await ready(create_app(Settings(), checks), make_client)

    assert status == 200
    assert body == {
        "status": "ready",
        "degraded": True,
        "checks": {"postgres": "ok", "gateway": "ok", "redis": "fail"},
    }


@pytest.mark.parametrize(
    ("postgres", "gateway"),
    [(False, True), (True, False), (False, False)],
)
async def test_not_ready_when_a_required_check_fails(
    make_client: ClientFactory, postgres: bool, gateway: bool
) -> None:
    checks = ReadinessChecks(postgres=constant(postgres), gateway=constant(gateway))

    status, body = await ready(create_app(Settings(), checks), make_client)

    assert status == 503
    assert body["status"] == "not_ready"
    assert body["checks"] == {
        "postgres": "ok" if postgres else "fail",
        "gateway": "ok" if gateway else "fail",
    }


async def test_check_that_raises_counts_as_fail(make_client: ClientFactory) -> None:
    async def broken() -> bool:
        raise RuntimeError("bug in check")

    checks = ReadinessChecks(postgres=broken, gateway=constant(True))

    status, body = await ready(create_app(Settings(), checks), make_client)

    assert status == 503
    assert body["checks"]["postgres"] == "fail"


class FakeWriter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        raise ConnectionResetError


async def test_tcp_check_passes_and_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = FakeWriter()
    calls: list[tuple[str, int]] = []

    async def fake_open(host: str, port: int) -> tuple[None, FakeWriter]:
        calls.append((host, port))
        return None, writer

    monkeypatch.setattr(health, "open_connection", fake_open)

    assert await tcp_check("postgres", 5432)() is True
    assert calls == [("postgres", 5432)]
    assert writer.closed


async def test_tcp_check_fails_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(host: str, port: int) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(health, "open_connection", refuse)

    assert await tcp_check("postgres", 5432)() is False


async def hang() -> bool:
    await asyncio.Event().wait()
    return True


async def test_stalled_optional_check_times_out_as_degraded(make_client: ClientFactory) -> None:
    checks = ReadinessChecks(postgres=constant(True), gateway=constant(True), redis=hang)
    app = create_app(Settings(readiness_timeout_s=0.05), checks)

    # Guard: without the per-check deadline the probe would never answer.
    async with asyncio.timeout(2):
        status, body = await ready(app, make_client)

    assert status == 200
    assert body["degraded"] is True
    assert body["checks"]["redis"] == "fail"


async def test_stalled_required_check_times_out_as_not_ready(make_client: ClientFactory) -> None:
    checks = ReadinessChecks(postgres=hang, gateway=constant(True))
    app = create_app(Settings(readiness_timeout_s=0.05), checks)

    async with asyncio.timeout(2):
        status, body = await ready(app, make_client)

    assert status == 503
    assert body["checks"]["postgres"] == "fail"


def test_checks_from_settings_adds_redis_only_when_configured() -> None:
    assert checks_from_settings(Settings(redis_host=None)).redis is None
    assert checks_from_settings(Settings(redis_host="redis")).redis is not None


def test_not_ready_checks_require_a_failed_required_check() -> None:
    with pytest.raises(ValidationError, match="failed postgres or gateway"):
        NotReadyChecks.model_validate({"postgres": "ok", "gateway": "ok", "redis": "fail"})


def test_ready_checks_reject_failed_required_or_unknown_result() -> None:
    with pytest.raises(ValidationError):
        ReadyChecks.model_validate({"postgres": "fail", "gateway": "ok"})
    with pytest.raises(ValidationError):
        ReadyChecks.model_validate({"postgres": "ok", "gateway": "ok", "redis": "maybe"})


def test_readiness_schema_uses_a_model_per_status() -> None:
    operation = create_app(Settings()).openapi()["paths"]["/internal/health/ready"]["get"]
    responses = operation["responses"]

    def ref(code: str) -> str:
        schema_ref: str = responses[code]["content"]["application/json"]["schema"]["$ref"]
        return schema_ref.rsplit("/", 1)[-1]

    assert ref("200") == "ReadinessReady"
    assert ref("503") == "ReadinessNotReady"


CONTRACT = Path(__file__).parents[4] / "api" / "openapi" / "internal-ai.yaml"


def test_not_ready_schema_matches_contract_any_of() -> None:
    contract = yaml.safe_load(CONTRACT.read_text())
    contract_rule = contract["components"]["schemas"]["NotReadyChecks"]["allOf"][1]["anyOf"]

    exported = create_app(Settings()).openapi()["components"]["schemas"]["NotReadyChecks"]

    assert exported["anyOf"] == contract_rule
