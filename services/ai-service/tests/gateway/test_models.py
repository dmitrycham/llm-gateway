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

WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


NOW = 1_000.0


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ai_service.gateway.models.monotonic", lambda: NOW)


@pytest.mark.usefixtures("frozen_clock")
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
def test_headers_keep_safety_margin(remaining_s: float, want_deadline_ms: str) -> None:
    context = CallContext(workspace_id=WORKSPACE_ID, request_id="req-1", deadline=NOW + remaining_s)

    headers = context.headers()

    assert headers[HEADER_REQUEST_ID] == "req-1"
    assert headers[HEADER_DEADLINE_MS] == want_deadline_ms


@pytest.mark.usefixtures("frozen_clock")
def test_headers_custom_margin() -> None:
    context = CallContext(workspace_id=WORKSPACE_ID, request_id="req-1", deadline=NOW + 1.0)

    assert context.headers(margin_ms=200)[HEADER_DEADLINE_MS] == "800"


@pytest.mark.usefixtures("frozen_clock")
@pytest.mark.parametrize("deadline_offset_s", [-5.0, 10.0], ids=["expired", "active"])
def test_negative_margin_is_rejected(deadline_offset_s: float) -> None:
    context = CallContext(
        workspace_id=WORKSPACE_ID, request_id="req-1", deadline=NOW + deadline_offset_s
    )

    with pytest.raises(ValueError, match="margin_ms"):
        context.headers(margin_ms=-1)


@pytest.mark.usefixtures("frozen_clock")
def test_expired_uses_raw_deadline() -> None:
    assert CallContext(workspace_id=WORKSPACE_ID, request_id="r", deadline=NOW).expired()
    assert not CallContext(workspace_id=WORKSPACE_ID, request_id="r", deadline=NOW + 1).expired()


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
def test_invalid_requests_are_rejected(model: type[Any], fields: dict[str, Any]) -> None:
    context = CallContext.with_timeout(WORKSPACE_ID, "req-1", timeout_s=10)

    with pytest.raises(ValidationError):
        model(context=context, **fields)


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
