import json

import pytest

from ai_service.gateway import (
    GatewayBudgetExceededError,
    GatewayError,
    GatewayRateLimitedError,
    GatewayTimeoutError,
    GatewayUnavailableError,
    error_from_response,
)


def _body(code: str, *, retryable: bool) -> str:
    return json.dumps(
        {
            "error": {"code": code, "message": "details", "retryable": retryable},
            "request_id": "req-1",
        }
    )


@pytest.mark.parametrize(
    ("status", "body", "retry_after", "want_cls", "want_code", "want_retryable", "want_after"),
    [
        (504, _body("timeout", retryable=True), None, GatewayTimeoutError, "timeout", True, None),
        (
            429,
            _body("rate_limited", retryable=True),
            "3",
            GatewayRateLimitedError,
            "rate_limited",
            True,
            3.0,
        ),
        (
            429,
            _body("budget_exceeded", retryable=False),
            None,
            GatewayBudgetExceededError,
            "budget_exceeded",
            False,
            None,
        ),
        (
            503,
            _body("provider_unavailable", retryable=True),
            None,
            GatewayUnavailableError,
            "provider_unavailable",
            True,
            None,
        ),
        (
            503,
            _body("dependency_unavailable", retryable=True),
            None,
            GatewayUnavailableError,
            "dependency_unavailable",
            True,
            None,
        ),
        (
            400,
            _body("invalid_request", retryable=False),
            None,
            GatewayError,
            "invalid_request",
            False,
            None,
        ),
        (502, "<html>Bad Gateway</html>", None, GatewayError, "unexpected_response", True, None),
        (400, "", None, GatewayError, "unexpected_response", False, None),
        (
            429,
            _body("rate_limited", retryable=True),
            "Wed, 21 Oct 2026 07:28:00 GMT",
            GatewayRateLimitedError,
            "rate_limited",
            True,
            None,
        ),
        (
            429,
            _body("rate_limited", retryable=True),
            "-1",
            GatewayRateLimitedError,
            "rate_limited",
            True,
            None,
        ),
    ],
)
def test_error_from_response(
    status: int,
    body: str,
    retry_after: str | None,
    want_cls: type[GatewayError],
    want_code: str,
    want_retryable: bool,
    want_after: float | None,
) -> None:
    err = error_from_response(status, body, retry_after)

    assert type(err) is want_cls
    assert err.code == want_code
    assert err.retryable is want_retryable
    assert err.retry_after == want_after


@pytest.mark.parametrize(
    ("header", "want"),
    [
        ("0", 0.0),
        ("120", 120.0),
        ("1e999", None),
        ("9" * 400, None),
        ("1.5", None),
        (" 3", None),
        ("", None),
        ("٣", None),
    ],
    ids=[
        "zero",
        "integer",
        "exponent-inf",
        "huge-integer",
        "fraction",
        "space",
        "empty",
        "non-ascii",
    ],
)
def test_retry_after_accepts_only_finite_delta_seconds(header: str, want: float | None) -> None:
    err = error_from_response(429, _body("rate_limited", retryable=True), header)

    assert err.retry_after == want


def test_error_keeps_request_id_and_message() -> None:
    err = error_from_response(504, _body("timeout", retryable=True).encode())

    assert err.request_id == "req-1"
    assert err.message == "details"
    assert str(err) == "timeout: details"
