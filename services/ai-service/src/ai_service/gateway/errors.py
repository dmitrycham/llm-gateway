"""Typed errors for gateway calls, mapped from the unified error contract (spec 7.4)."""

import math

from pydantic import BaseModel, ValidationError


class GatewayError(Exception):
    """A gateway call failed. Python never retries it: retries belong to the gateway."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id
        self.retry_after = retry_after


class GatewayTimeoutError(GatewayError):
    pass


class GatewayRateLimitedError(GatewayError):
    pass


class GatewayBudgetExceededError(GatewayError):
    pass


class GatewayUnavailableError(GatewayError):
    pass


_ERRORS_BY_CODE: dict[str, type[GatewayError]] = {
    "timeout": GatewayTimeoutError,
    "rate_limited": GatewayRateLimitedError,
    "budget_exceeded": GatewayBudgetExceededError,
    "provider_unavailable": GatewayUnavailableError,
    "dependency_unavailable": GatewayUnavailableError,
}


class _ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool


class _ErrorEnvelope(BaseModel):
    error: _ErrorBody
    request_id: str | None = None


def _parse_retry_after(value: str | None) -> float | None:
    # Only RFC 9110 delta-seconds (non-negative integer); the gateway does not send HTTP dates.
    # "1e999" or a 400-digit number would become inf and postpone a retry forever.
    if value is None or not value.isascii() or not value.isdecimal():
        return None
    seconds = float(value)
    return seconds if math.isfinite(seconds) else None


def error_from_response(
    status: int,
    body: bytes | str,
    retry_after: str | None = None,
) -> GatewayError:
    """Build a typed error from a non-2xx gateway response."""
    retry_after_s = _parse_retry_after(retry_after)
    try:
        envelope = _ErrorEnvelope.model_validate_json(body)
    except ValidationError:
        # A proxy or a crashed gateway can answer with HTML or an empty body.
        return GatewayError(
            "unexpected_response",
            f"gateway returned HTTP {status} without a valid error body",
            retryable=status >= 500,
            retry_after=retry_after_s,
        )

    err = envelope.error
    error_cls = _ERRORS_BY_CODE.get(err.code, GatewayError)
    return error_cls(
        err.code,
        err.message,
        retryable=err.retryable,
        request_id=envelope.request_id,
        retry_after=retry_after_s,
    )
