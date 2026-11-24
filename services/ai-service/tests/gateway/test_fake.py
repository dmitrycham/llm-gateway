import math
import time
import uuid

import pytest

from ai_service.gateway import (
    CallContext,
    ChatMessage,
    ChatRequest,
    EmbedRequest,
    FakeGatewayClient,
    GatewayClient,
    GatewayTimeoutError,
    Operation,
)
from ai_service.gateway.fake import FAKE_ANSWER, fake_vector
from ai_service.gateway.models import DEFAULT_DEADLINE_MARGIN_MS

WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _context(timeout_s: float = 30.0) -> CallContext:
    return CallContext.with_timeout(WORKSPACE_ID, "req-1", timeout_s)


def _embed_request(*texts: str) -> EmbedRequest:
    return EmbedRequest(context=_context(), operation=Operation.EMBED_QUERY, texts=list(texts))


def test_fake_satisfies_protocol() -> None:
    client: GatewayClient = FakeGatewayClient()
    assert client is not None


def test_fake_vector_golden_value() -> None:
    # Pinned values prove the output does not change between runs or processes.
    expected_prefix = [-0.146216, -0.058637, -0.022023, 0.122753]
    vector = fake_vector("hello world", 16)
    assert vector[:4] == pytest.approx(expected_prefix, abs=1e-6)


async def test_embed_same_input_same_output() -> None:
    first = await FakeGatewayClient().embed(_embed_request("hello world", "second text"))
    second = await FakeGatewayClient().embed(_embed_request("hello world", "second text"))
    assert first.vectors == second.vectors


@pytest.mark.parametrize(
    ("text_a", "text_b"),
    [
        ("hello world", "hello world!"),
        ("a", "b"),
        ("", " "),
    ],
)
async def test_embed_different_input_different_output(text_a: str, text_b: str) -> None:
    result = await FakeGatewayClient().embed(_embed_request(text_a, text_b))
    assert result.vectors[0] != result.vectors[1]


@pytest.mark.parametrize("dimensions", [1, 8, 16, 1536])
async def test_embed_dimensions_and_unit_norm(dimensions: int) -> None:
    result = await FakeGatewayClient(dimensions=dimensions).embed(_embed_request("text"))
    assert result.dimensions == dimensions
    assert len(result.vectors[0]) == dimensions
    assert math.sqrt(sum(v * v for v in result.vectors[0])) == pytest.approx(1.0)


async def test_embed_keeps_input_order() -> None:
    result = await FakeGatewayClient().embed(_embed_request("one", "two", "three"))
    assert result.vectors == [fake_vector(t, 16) for t in ("one", "two", "three")]


async def test_chat_returns_fixed_answer_with_citation() -> None:
    client = FakeGatewayClient()
    request = ChatRequest(
        context=_context(),
        operation=Operation.GENERATE,
        messages=[ChatMessage(role="user", content="What is the refund policy?")],
        max_output_tokens=256,
    )

    first = await client.chat(request)
    second = await client.chat(request)

    assert "[S1]" in first.content
    assert first == second
    assert first.usage.input_tokens == 5
    assert client.calls == [request, request]


ANSWER_WORDS = len(FAKE_ANSWER.split())


@pytest.mark.parametrize(
    ("max_output_tokens", "want_content", "want_tokens", "want_finish"),
    [
        (1, "This", 1, "length"),
        (ANSWER_WORDS - 1, FAKE_ANSWER.rsplit(" ", 1)[0], ANSWER_WORDS - 1, "length"),
        (ANSWER_WORDS, FAKE_ANSWER, ANSWER_WORDS, "stop"),
        (1_000, FAKE_ANSWER, ANSWER_WORDS, "stop"),
    ],
    ids=["one-token", "cut-before-citation", "exact", "large-limit"],
)
async def test_chat_honours_max_output_tokens(
    max_output_tokens: int, want_content: str, want_tokens: int, want_finish: str
) -> None:
    request = ChatRequest(
        context=_context(),
        operation=Operation.GENERATE,
        messages=[ChatMessage(role="user", content="hi")],
        max_output_tokens=max_output_tokens,
    )

    result = await FakeGatewayClient().chat(request)

    assert result.content == want_content
    assert result.usage.output_tokens == want_tokens
    assert result.finish_reason == want_finish


@pytest.mark.parametrize("call", ["chat", "embed"])
@pytest.mark.parametrize(
    "remaining_s",
    [0.0, DEFAULT_DEADLINE_MARGIN_MS / 1000 / 2],
    ids=["expired", "less-than-margin"],
)
async def test_no_gateway_budget_raises_timeout(call: str, remaining_s: float) -> None:
    client = FakeGatewayClient()
    expired = CallContext(
        workspace_id=WORKSPACE_ID, request_id="req-2", deadline=time.monotonic() + remaining_s
    )

    with pytest.raises(GatewayTimeoutError) as exc_info:
        if call == "chat":
            await client.chat(
                ChatRequest(
                    context=expired,
                    operation=Operation.GENERATE,
                    messages=[ChatMessage(role="user", content="hi")],
                    max_output_tokens=16,
                )
            )
        else:
            await client.embed(
                EmbedRequest(context=expired, operation=Operation.EMBED_QUERY, texts=["hi"])
            )

    assert exc_info.value.retryable is True
    assert exc_info.value.request_id == "req-2"


def test_fake_rejects_non_positive_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        FakeGatewayClient(dimensions=0)
