"""Deterministic in-memory gateway for hermetic tests: no network, no secrets."""

import hashlib
import math

from ai_service.gateway.errors import GatewayTimeoutError
from ai_service.gateway.models import (
    CallContext,
    ChatRequest,
    ChatResult,
    EmbeddingResult,
    EmbedRequest,
    Usage,
)

FAKE_PROVIDER = "fake"
FAKE_CHAT_MODEL = "fake-chat"
FAKE_EMBED_MODEL = "fake-embed"
FAKE_ANSWER = "This is a fake answer based on the provided context [S1]."
DEFAULT_DIMENSIONS = 16


def _tokens(text: str) -> int:
    return len(text.split())


def fake_vector(text: str, dimensions: int) -> list[float]:
    """Unit vector derived from SHA-256 of the text.

    Built-in hash() is salted per process, so it would break
    "same input, same output across runs".
    """
    raw = bytearray()
    counter = 0
    while len(raw) < dimensions * 4:
        raw += hashlib.sha256(f"{counter}:{text}".encode()).digest()
        counter += 1
    values = [
        int.from_bytes(raw[i * 4 : i * 4 + 4], "big") / 0xFFFFFFFF * 2 - 1
        for i in range(dimensions)
    ]
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


class FakeGatewayClient:
    """Implements GatewayClient. Records every request in ``calls``."""

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        if dimensions <= 0:
            msg = "dimensions must be positive"
            raise ValueError(msg)
        self.dimensions = dimensions
        self.calls: list[ChatRequest | EmbedRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        self._check_deadline(request.context)
        # Honour the limit like a real model, so RAG tests can see an answer cut before [S1].
        words = FAKE_ANSWER.split()
        emitted = words[: request.max_output_tokens]
        return ChatResult(
            content=" ".join(emitted),
            provider=FAKE_PROVIDER,
            model=FAKE_CHAT_MODEL,
            usage=Usage(
                input_tokens=sum(_tokens(m.content) for m in request.messages),
                output_tokens=len(emitted),
            ),
            usage_source="estimated",
            finish_reason="length" if len(emitted) < len(words) else "stop",
            latency_ms=0,
        )

    async def embed(self, request: EmbedRequest) -> EmbeddingResult:
        self.calls.append(request)
        self._check_deadline(request.context)
        return EmbeddingResult(
            vectors=[fake_vector(t, self.dimensions) for t in request.texts],
            provider=FAKE_PROVIDER,
            model=FAKE_EMBED_MODEL,
            dimensions=self.dimensions,
            usage=Usage(input_tokens=sum(_tokens(t) for t in request.texts)),
            usage_source="estimated",
        )

    @staticmethod
    def _check_deadline(context: CallContext) -> None:
        # Same rule as the real client: no budget left after the margin means the gateway
        # receives a zero deadline and answers with a timeout.
        if context.gateway_budget_ms() == 0:
            raise GatewayTimeoutError(
                "timeout",
                "no time left for the gateway call",
                retryable=True,
                request_id=context.request_id,
            )
