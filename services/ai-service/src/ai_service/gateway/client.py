"""The interface the RAG service and worker use to reach models (ADR-003)."""

from typing import Protocol

from ai_service.gateway.models import ChatRequest, ChatResult, EmbeddingResult, EmbedRequest


class GatewayClient(Protocol):
    """All model calls go through the Go Gateway.

    Implementations must not retry or fall back: the gateway owns both,
    and a Python-side retry could pay for the same call twice.
    Failures are raised as ``GatewayError`` subclasses.
    """

    async def chat(self, request: ChatRequest) -> ChatResult: ...

    async def embed(self, request: EmbedRequest) -> EmbeddingResult: ...
