"""Request and result types for calls to the Go AI Gateway (ADR-003)."""

import math
from enum import StrEnum
from time import monotonic
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

HEADER_REQUEST_ID = "X-Request-ID"
HEADER_DEADLINE_MS = "X-Request-Deadline-Ms"
# Time kept back for the response to travel back to us; same margin as Go -> Python (spec 4.3).
DEFAULT_DEADLINE_MARGIN_MS = 2000


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Operation(StrEnum):
    CONDENSE = "condense"
    GENERATE = "generate"
    EMBED_QUERY = "embed_query"
    EMBED_DOCUMENT = "embed_document"


ChatOperation = Literal[Operation.CONDENSE, Operation.GENERATE]
EmbedOperation = Literal[Operation.EMBED_QUERY, Operation.EMBED_DOCUMENT]
UsageSource = Literal["provider", "estimated", "unknown"]


class CallContext(_Model):
    """Who pays for the call and how long the caller is still waiting."""

    workspace_id: UUID
    request_id: str = Field(min_length=1)
    # Absolute point on time.monotonic(): wall-clock jumps must not stretch a deadline.
    deadline: float

    @classmethod
    def with_timeout(cls, workspace_id: UUID, request_id: str, timeout_s: float) -> Self:
        return cls(
            workspace_id=workspace_id,
            request_id=request_id,
            deadline=monotonic() + timeout_s,
        )

    def remaining_ms(self) -> int:
        return max(0, math.floor((self.deadline - monotonic()) * 1000))

    def expired(self) -> bool:
        return self.remaining_ms() == 0

    def gateway_budget_ms(self, margin_ms: int = DEFAULT_DEADLINE_MARGIN_MS) -> int:
        """Time the gateway may spend, so its answer arrives before our own deadline."""
        # A negative margin would extend an expired deadline and allow a paid call after it.
        if margin_ms < 0:
            msg = "margin_ms must be non-negative"
            raise ValueError(msg)
        return max(0, self.remaining_ms() - margin_ms)

    def headers(self, margin_ms: int = DEFAULT_DEADLINE_MARGIN_MS) -> dict[str, str]:
        return {
            HEADER_REQUEST_ID: self.request_id,
            HEADER_DEADLINE_MS: str(self.gateway_budget_ms(margin_ms)),
        }


class Usage(_Model):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)


class ChatMessage(_Model):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(_Model):
    context: CallContext
    operation: ChatOperation
    messages: list[ChatMessage] = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ChatResult(_Model):
    content: str
    provider: str
    model: str
    usage: Usage
    usage_source: UsageSource
    finish_reason: str
    latency_ms: int = Field(ge=0)


class EmbedRequest(_Model):
    context: CallContext
    operation: EmbedOperation
    texts: list[str] = Field(min_length=1)


class EmbeddingResult(_Model):
    vectors: list[list[float]]
    provider: str
    model: str
    dimensions: int = Field(gt=0)
    usage: Usage
    usage_source: UsageSource

    @model_validator(mode="after")
    def _check_dimensions(self) -> Self:
        # A vector of the wrong size would corrupt the per-profile ANN index.
        for i, vector in enumerate(self.vectors):
            if len(vector) != self.dimensions:
                msg = f"vector {i} has {len(vector)} dimensions, expected {self.dimensions}"
                raise ValueError(msg)
        return self
