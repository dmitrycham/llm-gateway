import uuid

import pytest
from httpx import AsyncClient
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

from ai_service.api.request_id import (
    HEADER_REQUEST_ID,
    RequestIdMiddleware,
    get_request_id,
    is_valid_request_id,
)

UUID_ID = "0193a1f0-9d12-7c33-a1b2-6f8e9d0c1b2a"
ULID_ID = "01JDQ8Z6M3K4Y5W6V7T8S9R0QP"


@pytest.mark.parametrize("request_id", [UUID_ID, ULID_ID])
async def test_valid_request_id_is_echoed(client: AsyncClient, request_id: str) -> None:
    response = await client.get("/internal/health/live", headers={HEADER_REQUEST_ID: request_id})

    assert response.headers[HEADER_REQUEST_ID] == request_id


@pytest.mark.parametrize("headers", [{}, {HEADER_REQUEST_ID: "not-an-id"}])
async def test_missing_or_invalid_request_id_is_replaced(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/internal/health/live", headers=headers)

    generated = response.headers[HEADER_REQUEST_ID]
    assert is_valid_request_id(generated)
    assert generated != headers.get(HEADER_REQUEST_ID)


async def test_request_id_header_is_not_duplicated_on_errors(client: AsyncClient) -> None:
    response = await client.get("/nope", headers={HEADER_REQUEST_ID: UUID_ID})

    assert response.headers.get_list(HEADER_REQUEST_ID) == [UUID_ID]
    assert response.json()["request_id"] == UUID_ID


def test_get_request_id_without_middleware_creates_one() -> None:
    request = Request({"type": "http", "headers": []})

    assert uuid.UUID(get_request_id(request))


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (UUID_ID, True),
        (UUID_ID.upper(), True),
        (ULID_ID, True),
        ("81JDQ8Z6M3K4Y5W6V7T8S9R0QP", False),  # ULID first char above 7 overflows
        ("01JDQ8Z6M3K4Y5W6V7T8S9R0QU", False),  # U is not in Crockford base32
        (UUID_ID + "0", False),
        ("", False),
    ],
)
def test_is_valid_request_id(value: str, valid: bool) -> None:
    assert is_valid_request_id(value) is valid


async def test_non_http_scope_passes_through_unchanged() -> None:
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        del message

    scope: Scope = {"type": "lifespan"}
    await RequestIdMiddleware(inner)(scope, receive, send)

    assert seen == [{"type": "lifespan"}]
