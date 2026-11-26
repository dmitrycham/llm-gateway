"""Error bodies follow `ErrorResponse` from internal-ai.yaml, not FastAPI's defaults."""

import logging
from collections.abc import Callable
from typing import Annotated

import pytest
from fastapi import FastAPI, Header, HTTPException
from httpx import AsyncClient
from pydantic import BaseModel, Field

from ai_service.api.errors import ErrorResponse
from ai_service.api.request_id import HEADER_REQUEST_ID

ClientFactory = Callable[[FastAPI], AsyncClient]

REQUEST_ID = "0193a1f0-9d12-7c33-a1b2-6f8e9d0c1b2a"


MAX_MESSAGE = 2000
SESSION_ID = "0193a1f0-7c2e-7a10-9b55-3f6c2d9e8a01"
HEADERS = {"X-Request-Deadline-Ms": "1000", HEADER_REQUEST_ID: REQUEST_ID}


class EchoBody(BaseModel):
    session_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    # Same limit as the contract example for `message_too_long`.
    message: str = Field(max_length=MAX_MESSAGE)


@pytest.fixture
async def test_client(app: FastAPI, make_client: ClientFactory) -> AsyncClient:
    """App with test-only routes: the ticket has no business endpoint yet."""

    @app.post("/test/echo")
    async def echo(
        body: EchoBody,
        deadline: Annotated[int, Header(alias="X-Request-Deadline-Ms", ge=1)],
    ) -> dict[str, str]:
        return {"message": body.message}

    @app.get("/test/teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=418, detail="No coffee here")

    @app.get("/test/http-500")
    async def http_500() -> None:
        raise HTTPException(status_code=503)

    @app.get("/test/crash")
    async def crash() -> None:
        raise RuntimeError("secret internal detail")

    return make_client(app)


def parse(body: object) -> ErrorResponse:
    # Round-trip through the model: extra or missing fields fail the test.
    return ErrorResponse.model_validate(body)


async def test_broken_field_is_422_validation_failed(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.post(
            "/test/echo",
            json={"session_id": "secret-user-input", "message": 42},
            headers=HEADERS,
        )

    assert response.status_code == 422
    error = parse(response.json())
    assert error.request_id == REQUEST_ID
    assert error.error.code == "validation_failed"
    assert error.error.retryable is False
    assert error.error.details is not None
    paths = [field["path"] for field in error.error.details["fields"]]
    assert paths == ["session_id", "message"]
    # User input must not leak into the error body.
    assert "secret-user-input" not in response.text


async def test_oversized_message_is_413_message_too_long(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.post(
            "/test/echo",
            json={"session_id": SESSION_ID, "message": "x" * (MAX_MESSAGE + 1)},
            headers=HEADERS,
        )

    assert response.status_code == 413
    error = parse(response.json())
    assert error.error.code == "message_too_long"
    assert error.error.retryable is False
    assert error.error.details == {"max_length": MAX_MESSAGE}


async def test_oversized_message_wins_over_other_field_errors(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.post(
            "/test/echo",
            json={"session_id": "nope", "message": "x" * (MAX_MESSAGE + 1)},
            headers=HEADERS,
        )

    assert response.status_code == 413
    assert parse(response.json()).error.code == "message_too_long"


async def test_message_at_limit_is_accepted(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.post(
            "/test/echo",
            json={"session_id": SESSION_ID, "message": "x" * MAX_MESSAGE},
            headers=HEADERS,
        )

    assert response.status_code == 200


async def test_missing_header_is_400_invalid_request(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.post("/test/echo", json={"session_id": "x", "message": "hi"})

    assert response.status_code == 400
    error = parse(response.json())
    assert error.error.code == "invalid_request"
    assert error.error.details == {"header": "X-Request-Deadline-Ms"}


async def test_invalid_json_is_400_invalid_request(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.post(
            "/test/echo",
            content=b"{not json",
            headers={"Content-Type": "application/json", "X-Request-Deadline-Ms": "1000"},
        )

    assert response.status_code == 400
    error = parse(response.json())
    assert error.error.code == "invalid_request"
    assert error.error.message == "Request body is not valid JSON"


async def test_empty_body_is_400_invalid_request(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.post(
            "/test/echo", content=b"", headers={**HEADERS, "Content-Type": "application/json"}
        )

    assert response.status_code == 400
    error = parse(response.json())
    assert error.error.code == "invalid_request"
    assert error.error.message == "Request body is not valid JSON"


async def test_json_null_body_stays_422(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.post(
            "/test/echo", content=b"null", headers={**HEADERS, "Content-Type": "application/json"}
        )

    assert response.status_code == 422
    assert parse(response.json()).error.code == "validation_failed"


async def test_unknown_path_is_404_in_shared_format(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.get("/nope")

    assert response.status_code == 404
    error = parse(response.json())
    assert error.error.code == "invalid_request"
    assert error.error.message == "Not Found"


async def test_wrong_method_keeps_allow_header(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.delete("/internal/health/live")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert parse(response.json()).error.code == "invalid_request"


async def test_http_exception_keeps_status_and_detail(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.get("/test/teapot")

    assert response.status_code == 418
    assert parse(response.json()).error.message == "No coffee here"


async def test_http_5xx_exception_is_internal_error(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.get("/test/http-500")

    assert response.status_code == 500
    assert parse(response.json()).error.code == "internal_error"


async def test_unhandled_error_is_500_without_details(test_client: AsyncClient) -> None:
    async with test_client as client:
        response = await client.get("/test/crash", headers={HEADER_REQUEST_ID: REQUEST_ID})

    assert response.status_code == 500
    error = parse(response.json())
    assert error.error.code == "internal_error"
    assert error.error.retryable is False
    assert error.request_id == REQUEST_ID
    assert response.headers[HEADER_REQUEST_ID] == REQUEST_ID
    assert "secret internal detail" not in response.text


async def test_unhandled_error_log_line_carries_request_id(
    test_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="ai_service.api.errors"):
        async with test_client as client:
            response = await client.get("/test/crash")

    request_id = response.headers[HEADER_REQUEST_ID]
    messages = [r.getMessage() for r in caplog.records if r.name == "ai_service.api.errors"]
    assert messages == [f"unhandled error RuntimeError request_id={request_id}"]
