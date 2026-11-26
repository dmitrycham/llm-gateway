"""Fixtures for HTTP API tests."""

from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ai_service.api import create_app
from ai_service.api.health import ReadinessChecks
from ai_service.api.settings import Settings


async def _passing() -> bool:
    return True


@pytest.fixture
def checks() -> ReadinessChecks:
    return ReadinessChecks(postgres=_passing, gateway=_passing)


@pytest.fixture
def app(checks: ReadinessChecks) -> FastAPI:
    return create_app(Settings(), checks)


ClientFactory = Callable[[FastAPI], AsyncClient]


@pytest.fixture
def make_client() -> ClientFactory:
    # raise_app_exceptions=False: ServerErrorMiddleware re-raises after sending the 500,
    # and the test needs the response, as a real server would send it.
    def make(app: FastAPI) -> AsyncClient:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        return AsyncClient(transport=transport, base_url="http://test")

    return make


@pytest.fixture
async def client(app: FastAPI, make_client: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with make_client(app) as ac:
        yield ac
