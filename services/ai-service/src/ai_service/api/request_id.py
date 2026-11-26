"""``X-Request-ID`` handling: reuse a valid incoming ID or create one, echo it back."""

import re
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER_REQUEST_ID = "X-Request-ID"
_STATE_KEY = "request_id"

# Same patterns as `RequestId` in internal-ai.yaml: UUID or ULID.
_UUID = re.compile(r"[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")
_ULID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{25}")


def is_valid_request_id(value: str) -> bool:
    return bool(_UUID.fullmatch(value) or _ULID.fullmatch(value))


def new_request_id() -> str:
    return str(uuid.uuid4())


def get_request_id(request: Request) -> str:
    """Return the ID the middleware stored, or a new one if the middleware did not run."""
    value = getattr(request.state, _STATE_KEY, None)
    return value if isinstance(value, str) else new_request_id()


class RequestIdMiddleware:
    """Pure ASGI middleware, not ``BaseHTTPMiddleware``: that one buffers and breaks SSE.

    An invalid incoming ID is replaced, not rejected: the error body must carry a valid
    ``request_id`` (internal-ai.yaml), and a required-header check on `/internal/v1/*`
    still answers 400 through the route's own header validation.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(HEADER_REQUEST_ID)
        request_id = (
            incoming if incoming is not None and is_valid_request_id(incoming) else new_request_id()
        )
        # Stored in the shared scope dict, so handlers outside this middleware (the 500
        # handler in ServerErrorMiddleware) see the same ID.
        scope.setdefault("state", {})[_STATE_KEY] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER_REQUEST_ID] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)
