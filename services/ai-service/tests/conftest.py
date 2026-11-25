"""Shared fixtures for ai-service tests."""

import uuid

import pytest

from ai_service.gateway import CallContext, FakeGatewayClient

FROZEN_NOW = 1_000.0


@pytest.fixture
def workspace_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> float:
    """Freeze the gateway clock and return the frozen ``monotonic()`` value.

    Patch the module attribute, not ``time.monotonic``: asyncio uses the latter.
    """
    monkeypatch.setattr("ai_service.gateway.models.monotonic", lambda: FROZEN_NOW)
    return FROZEN_NOW


@pytest.fixture
def call_context(workspace_id: uuid.UUID, frozen_clock: float) -> CallContext:
    # Depends on frozen_clock so the deadline is created and read with the same clock,
    # whatever order a test requests fixtures in. Otherwise a real-clock deadline read
    # against the frozen clock leaves days of budget and hides deadline regressions.
    del frozen_clock
    return CallContext.with_timeout(workspace_id, "req-1", timeout_s=30.0)


@pytest.fixture
def fake_client() -> FakeGatewayClient:
    return FakeGatewayClient()
