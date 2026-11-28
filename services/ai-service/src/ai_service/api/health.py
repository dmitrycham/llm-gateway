"""Liveness and readiness probes (internal-ai.yaml, tag `health`)."""

import asyncio
import contextlib
import logging
from asyncio import open_connection
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Literal, Self

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_service.api.settings import Settings

logger = logging.getLogger(__name__)

Check = Callable[[], Awaitable[bool]]
CheckResult = Literal["ok", "fail"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Checks(BaseModel):
    """`ReadinessChecks` in the contract: required keys plus optional ones such as `redis`."""

    model_config = ConfigDict(frozen=True, extra="allow")
    # Optional checks are extra keys, validated as `CheckResult`.
    __pydantic_extra__: dict[str, CheckResult] = Field(init=False)


class ReadyChecks(_Checks):
    postgres: Literal["ok"]
    gateway: Literal["ok"]


class NotReadyChecks(_Checks):
    # The contract's `anyOf`, so the exported schema rejects an all-healthy 503 too.
    model_config = ConfigDict(
        json_schema_extra={
            "anyOf": [
                {"properties": {"postgres": {"const": "fail"}}},
                {"properties": {"gateway": {"const": "fail"}}},
            ],
        },
    )

    postgres: CheckResult
    gateway: CheckResult

    # Same rule at runtime: `json_schema_extra` only changes the exported schema.
    @model_validator(mode="after")
    def _one_required_check_fails(self) -> Self:
        if "fail" not in (self.postgres, self.gateway):
            msg = "not ready requires a failed postgres or gateway check"
            raise ValueError(msg)
        return self


class LivenessStatus(_Model):
    status: Literal["ok"]


class ReadinessReady(_Model):
    status: Literal["ready"]
    # `true` when an optional dependency such as Redis is down (spec 9.3).
    degraded: bool
    checks: ReadyChecks


class ReadinessNotReady(_Model):
    status: Literal["not_ready"]
    degraded: bool
    checks: NotReadyChecks


@dataclass(frozen=True)
class ReadinessChecks:
    """`postgres` and `gateway` decide readiness; `redis` only sets `degraded` (spec 9.3)."""

    postgres: Check
    gateway: Check
    redis: Check | None = None


def tcp_check(host: str, port: int) -> Check:
    """Pass when a TCP connection opens. The deadline comes from the caller (`_run`).

    Placeholder until the PostgreSQL pool and the real GatewayClient exist; their tickets
    replace it with `SELECT 1` and a gateway call.
    """

    async def check() -> bool:
        try:
            _, writer = await open_connection(host, port)
        except OSError:
            return False
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return True

    return check


def checks_from_settings(settings: Settings) -> ReadinessChecks:
    redis = (
        tcp_check(settings.redis_host, settings.redis_port)
        if settings.redis_host is not None
        else None
    )
    return ReadinessChecks(
        postgres=tcp_check(settings.postgres_host, settings.postgres_port),
        gateway=tcp_check(settings.gateway_host, settings.gateway_port),
        redis=redis,
    )


async def _run(name: str, check: Check, timeout_s: float) -> CheckResult:
    # One deadline for the whole check, so a stalled one cannot hold the probe response.
    try:
        async with asyncio.timeout(timeout_s):
            return "ok" if await check() else "fail"
    except TimeoutError:
        logger.warning("readiness check %s timed out", name)
        return "fail"
    # A broken check must report `fail`, not turn the probe into a 500.
    except Exception:
        logger.exception("readiness check %s raised", name)
        return "fail"


def build_health_router(checks: ReadinessChecks, timeout_s: float) -> APIRouter:
    router = APIRouter(prefix="/internal/health", tags=["health"])

    # AC1 demo (LGW-27), revert before merge: a response the contract does not have.
    @router.get(
        "/live",
        operation_id="getLiveness",
        responses={HTTPStatus.INTERNAL_SERVER_ERROR: {"description": "AC1 demo"}},
    )
    async def live() -> LivenessStatus:
        return LivenessStatus(status="ok")

    @router.get(
        "/ready",
        operation_id="getReadiness",
        response_model=ReadinessReady,
        responses={
            HTTPStatus.OK: {"description": "Ready to serve traffic, possibly degraded."},
            HTTPStatus.SERVICE_UNAVAILABLE: {
                "model": ReadinessNotReady,
                "description": "Not ready. At least one required check failed.",
            },
        },
    )
    async def ready() -> JSONResponse:
        named = {"postgres": checks.postgres, "gateway": checks.gateway}
        if checks.redis is not None:
            named["redis"] = checks.redis
        results = await asyncio.gather(
            *(_run(name, check, timeout_s) for name, check in named.items())
        )
        by_name = dict(zip(named, results, strict=True))
        degraded = by_name.get("redis") == "fail"

        if by_name["postgres"] == "ok" and by_name["gateway"] == "ok":
            ready_body = ReadinessReady(
                status="ready", degraded=degraded, checks=ReadyChecks.model_validate(by_name)
            )
            return JSONResponse(ready_body.model_dump(mode="json"))

        not_ready_body = ReadinessNotReady(
            status="not_ready", degraded=degraded, checks=NotReadyChecks.model_validate(by_name)
        )
        return JSONResponse(
            not_ready_body.model_dump(mode="json"),
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )

    return router
