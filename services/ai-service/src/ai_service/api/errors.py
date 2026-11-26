"""Single error format of internal-ai.yaml (`ErrorResponse`) and the handlers that emit it."""

import logging
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from ai_service.api.request_id import HEADER_REQUEST_ID, get_request_id

logger = logging.getLogger(__name__)

ErrorCode = Literal[
    "invalid_request",
    "unauthenticated",
    "request_in_progress",
    "message_too_long",
    "validation_failed",
    "idempotency_key_reuse",
    "rate_limited",
    "budget_exceeded",
    "provider_error",
    "provider_unavailable",
    "dependency_unavailable",
    "timeout",
    "internal_error",
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Error(_Model):
    code: ErrorCode
    # Never holds secrets or user message text (internal-ai.yaml).
    message: str
    retryable: bool
    details: dict[str, Any] | None = None


class ErrorResponse(_Model):
    error: Error
    request_id: str


def error_response(
    request: Request,
    status_code: int,
    error: Error,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    body = ErrorResponse(error=error, request_id=request_id)
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True),
        status_code=status_code,
        # Set here too: the 500 handler runs outside RequestIdMiddleware.
        headers={**(headers or {}), HEADER_REQUEST_ID: request_id},
    )


def _is_request_error(error: Mapping[str, Any]) -> bool:
    """A bad header or a body that is not JSON is 400; a broken field rule is 422."""
    loc = tuple(error.get("loc", ()))
    return (
        error.get("type") == "json_invalid"
        or (len(loc) > 0 and loc[0] == "header")
        # Empty body on a route that requires one: no JSON value at all, not a field rule.
        # A JSON `null` fails with another error type and stays 422.
        or (error.get("type") == "missing" and loc == ("body",))
    )


def _is_message_too_long(error: Mapping[str, Any]) -> bool:
    return (
        tuple(error.get("loc", ())) == ("body", "message")
        and error.get("type") == "string_too_long"
    )


def _field_path(loc: Sequence[Any]) -> str:
    # Body fields are reported as in the contract (`session_id`, not `body.session_id`);
    # other sources keep their prefix (`query.limit`).
    parts = loc[1:] if loc and loc[0] == "body" else loc
    return ".".join(str(part) for part in parts)


async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors: Sequence[Mapping[str, Any]] = exc.errors()
    request_errors = [e for e in errors if _is_request_error(e)]
    if request_errors:
        first = request_errors[0]
        if first["loc"][0] == "body":
            error = Error(
                code="invalid_request",
                message="Request body is not valid JSON",
                retryable=False,
            )
        else:
            header = str(first["loc"][-1])
            error = Error(
                code="invalid_request",
                message=f"Header {header} is missing or invalid",
                retryable=False,
                details={"header": header},
            )
        return error_response(request, HTTPStatus.BAD_REQUEST, error)

    # The one exception to 422 in internal-ai.yaml: an oversized `message` is 413, even
    # when other fields are broken too.
    too_long = next((e for e in errors if _is_message_too_long(e)), None)
    if too_long is not None:
        max_length = too_long.get("ctx", {}).get("max_length")
        error = Error(
            code="message_too_long",
            message="The message exceeds the maximum length",
            retryable=False,
            details={"max_length": max_length} if max_length is not None else None,
        )
        return error_response(request, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, error)

    # `msg` only: `input` may echo user text, which must not reach the error body.
    fields = [{"path": _field_path(e.get("loc", ())), "reason": e.get("msg", "")} for e in errors]
    error = Error(
        code="validation_failed",
        message="Request body failed validation",
        retryable=False,
        details={"fields": fields},
    )
    return error_response(request, HTTPStatus.UNPROCESSABLE_ENTITY, error)


async def _on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return _internal_error(request)
    message = exc.detail if exc.detail else HTTPStatus(exc.status_code).phrase
    error = Error(code="invalid_request", message=message, retryable=False)
    # Keep headers such as `Allow` on 405.
    return error_response(request, exc.status_code, error, headers=exc.headers)


def _internal_error(request: Request) -> JSONResponse:
    # Not retryable: the failure may have happened after a paid gateway call.
    error = Error(code="internal_error", message="Internal server error", retryable=False)
    return error_response(request, HTTPStatus.INTERNAL_SERVER_ERROR, error)


async def _on_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    # No traceback here: ServerErrorMiddleware re-raises after the response, and the
    # server logs it. This line links that traceback to the request ID. The ID is in the
    # text, not only in `extra`: the default logging setup does not print extra fields.
    request_id = get_request_id(request)
    logger.error(
        "unhandled error %s request_id=%s",
        type(exc).__name__,
        request_id,
        extra={"request_id": request_id},
    )
    return _internal_error(request)


def install_error_handlers(app: FastAPI) -> None:
    """Replace FastAPI's default error bodies with the shared `ErrorResponse`."""
    app.exception_handler(RequestValidationError)(_on_validation_error)
    # Starlette's class, not FastAPI's subclass: router 404/405 raise the Starlette one.
    app.exception_handler(StarletteHTTPException)(_on_http_error)
    # Runs in ServerErrorMiddleware, outside RequestIdMiddleware.
    app.exception_handler(Exception)(_on_unhandled_error)
