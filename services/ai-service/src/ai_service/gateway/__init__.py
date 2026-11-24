"""Client side of the Go AI Gateway: types, errors and a test fake."""

from ai_service.gateway.client import GatewayClient
from ai_service.gateway.errors import (
    GatewayBudgetExceededError,
    GatewayError,
    GatewayRateLimitedError,
    GatewayTimeoutError,
    GatewayUnavailableError,
    error_from_response,
)
from ai_service.gateway.fake import FakeGatewayClient
from ai_service.gateway.models import (
    CallContext,
    ChatMessage,
    ChatRequest,
    ChatResult,
    EmbeddingResult,
    EmbedRequest,
    Operation,
    Usage,
)

__all__ = [
    "CallContext",
    "ChatMessage",
    "ChatRequest",
    "ChatResult",
    "EmbedRequest",
    "EmbeddingResult",
    "FakeGatewayClient",
    "GatewayBudgetExceededError",
    "GatewayClient",
    "GatewayError",
    "GatewayRateLimitedError",
    "GatewayTimeoutError",
    "GatewayUnavailableError",
    "Operation",
    "Usage",
    "error_from_response",
]
