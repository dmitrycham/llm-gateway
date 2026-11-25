import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from ai_service.gateway import (
    CallContext,
    ChatMessage,
    ChatRequest,
    EmbeddingResult,
    EmbedRequest,
    Operation,
    Usage,
)
from ai_service.gateway.models import (
    DEFAULT_DEADLINE_MARGIN_MS,
    HEADER_DEADLINE_MS,
    HEADER_REQUEST_ID,
)


@pytest.mark.parametrize(
    ("remaining_s", "want_deadline_ms"),
    [
        (10.0, str(10_000 - DEFAULT_DEADLINE_MARGIN_MS)),
        (DEFAULT_DEADLINE_MARGIN_MS / 1000 + 0.5, "500"),
        (DEFAULT_DEADLINE_MARGIN_MS / 1000, "0"),
        (1.0, "0"),
        (-5.0, "0"),
    ],
    ids=["normal", "just-above-margin", "equal-to-margin", "below-margin", "already-expired"],
)
def test_headers_keep_safety_margin(
    frozen_clock: float, workspace_id: uuid.UUID, remaining_s: float, want_deadline_ms: str
) -> None:
    context = CallContext(
        workspace_id=workspace_id, request_id="req-1", deadline=frozen_clock + remaining_s
    )

    headers = context.headers()

    assert headers[HEADER_REQUEST_ID] == "req-1"
    assert headers[HEADER_DEADLINE_MS] == want_deadline_ms


def test_headers_custom_margin(frozen_clock: float, workspace_id: uuid.UUID) -> None:
    context = CallContext(
        workspace_id=workspace_id, request_id="req-1", deadline=frozen_clock + 1.0
    )

    assert context.headers(margin_ms=200)[HEADER_DEADLINE_MS] == "800"


@pytest.mark.parametrize("deadline_offset_s", [-5.0, 10.0], ids=["expired", "active"])
def test_negative_margin_is_rejected(
    frozen_clock: float, workspace_id: uuid.UUID, deadline_offset_s: float
) -> None:
    context = CallContext(
        workspace_id=workspace_id, request_id="req-1", deadline=frozen_clock + deadline_offset_s
    )

    with pytest.raises(ValueError, match="margin_ms"):
        context.headers(margin_ms=-1)


def test_expired_uses_raw_deadline(frozen_clock: float, workspace_id: uuid.UUID) -> None:
    now = frozen_clock
    assert CallContext(workspace_id=workspace_id, request_id="r", deadline=now).expired()
    assert not CallContext(workspace_id=workspace_id, request_id="r", deadline=now + 1).expired()


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (
            ChatRequest,
            {
                "operation": Operation.EMBED_QUERY,
                "messages": [ChatMessage(role="user", content="hi")],
                "max_output_tokens": 1,
            },
        ),
        (ChatRequest, {"operation": Operation.GENERATE, "messages": [], "max_output_tokens": 1}),
        (
            ChatRequest,
            {
                "operation": Operation.GENERATE,
                "messages": [ChatMessage(role="user", content="hi")],
                "max_output_tokens": 0,
            },
        ),
        (EmbedRequest, {"operation": Operation.GENERATE, "texts": ["hi"]}),
        (EmbedRequest, {"operation": Operation.EMBED_DOCUMENT, "texts": []}),
        (EmbedRequest, {"operation": Operation.EMBED_QUERY, "texts": ["hi"], "extra": 1}),
    ],
    ids=[
        "chat-with-embed-operation",
        "chat-without-messages",
        "chat-zero-max-tokens",
        "embed-with-chat-operation",
        "embed-without-texts",
        "embed-unknown-field",
    ],
)
def test_invalid_requests_are_rejected(
    call_context: CallContext, model: type[Any], fields: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        model(context=call_context, **fields)


def test_embedding_result_rejects_wrong_dimensions() -> None:
    with pytest.raises(ValidationError, match="expected 3"):
        EmbeddingResult(
            vectors=[[0.1, 0.2, 0.3], [0.1, 0.2]],
            provider="fake",
            model="fake-embed",
            dimensions=3,
            usage=Usage(input_tokens=2),
            usage_source="estimated",
        )


def test_call_context_fixture_uses_frozen_clock(
    call_context: CallContext, frozen_clock: float
) -> None:
    # call_context is requested first on purpose: the deadline must still use the frozen clock.
    assert call_context.deadline == frozen_clock + 30.0
