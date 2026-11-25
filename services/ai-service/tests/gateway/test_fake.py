import asyncio
import math
import time
import uuid

import pytest

from ai_service.gateway import (
    CallContext,
    ChatMessage,
    ChatRequest,
    ChatResult,
    EmbeddingResult,
    EmbedRequest,
    FakeGatewayClient,
    GatewayClient,
    GatewayTimeoutError,
    Operation,
)
from ai_service.gateway.fake import FAKE_ANSWER, fake_vector
from ai_service.gateway.models import DEFAULT_DEADLINE_MARGIN_MS


def _embed_request(context: CallContext, *texts: str) -> EmbedRequest:
    return EmbedRequest(context=context, operation=Operation.EMBED_QUERY, texts=list(texts))


async def _call(
    client: GatewayClient, call: str, context: CallContext
) -> ChatResult | EmbeddingResult:
    if call == "chat":
        return await client.chat(
            ChatRequest(
                context=context,
                operation=Operation.GENERATE,
                messages=[ChatMessage(role="user", content="hi")],
                max_output_tokens=16,
            )
        )
    return await client.embed(_embed_request(context, "hi"))


def test_fake_satisfies_protocol() -> None:
    client: GatewayClient = FakeGatewayClient()
    assert client is not None


def test_fake_vector_golden_value() -> None:
    # Pinned values prove the output does not change between runs or processes.
    expected_prefix = [-0.146216, -0.058637, -0.022023, 0.122753]
    vector = fake_vector("hello world", 16)
    assert vector[:4] == pytest.approx(expected_prefix, abs=1e-6)


async def test_embed_same_input_same_output(call_context: CallContext) -> None:
    # Two separate clients: determinism must not depend on per-instance state.
    request = _embed_request(call_context, "hello world", "second text")
    first = await FakeGatewayClient().embed(request)
    second = await FakeGatewayClient().embed(request)
    assert first.vectors == second.vectors


@pytest.mark.parametrize(
    ("text_a", "text_b"),
    [
        ("hello world", "hello world!"),
        ("a", "b"),
        ("", " "),
    ],
)
async def test_embed_different_input_different_output(
    fake_client: FakeGatewayClient, call_context: CallContext, text_a: str, text_b: str
) -> None:
    result = await fake_client.embed(_embed_request(call_context, text_a, text_b))
    assert result.vectors[0] != result.vectors[1]


@pytest.mark.parametrize("dimensions", [1, 8, 16, 1536])
async def test_embed_dimensions_and_unit_norm(call_context: CallContext, dimensions: int) -> None:
    client = FakeGatewayClient(dimensions=dimensions)
    result = await client.embed(_embed_request(call_context, "text"))
    assert result.dimensions == dimensions
    assert len(result.vectors[0]) == dimensions
    assert math.sqrt(sum(v * v for v in result.vectors[0])) == pytest.approx(1.0)


async def test_embed_keeps_input_order(
    fake_client: FakeGatewayClient, call_context: CallContext
) -> None:
    result = await fake_client.embed(_embed_request(call_context, "one", "two", "three"))
    assert result.vectors == [fake_vector(t, 16) for t in ("one", "two", "three")]


async def test_chat_returns_fixed_answer_with_citation(
    fake_client: FakeGatewayClient, call_context: CallContext
) -> None:
    request = ChatRequest(
        context=call_context,
        operation=Operation.GENERATE,
        messages=[ChatMessage(role="user", content="What is the refund policy?")],
        max_output_tokens=256,
    )

    first = await fake_client.chat(request)
    second = await fake_client.chat(request)

    assert "[S1]" in first.content
    assert first == second
    assert first.usage.input_tokens == 5
    assert fake_client.calls == [request, request]


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
    fake_client: FakeGatewayClient,
    call_context: CallContext,
    max_output_tokens: int,
    want_content: str,
    want_tokens: int,
    want_finish: str,
) -> None:
    request = ChatRequest(
        context=call_context,
        operation=Operation.GENERATE,
        messages=[ChatMessage(role="user", content="hi")],
        max_output_tokens=max_output_tokens,
    )

    result = await fake_client.chat(request)

    assert result.content == want_content
    assert result.usage.output_tokens == want_tokens
    assert result.finish_reason == want_finish


@pytest.mark.parametrize("call", ["chat", "embed"])
@pytest.mark.parametrize(
    "remaining_s",
    [0.0, DEFAULT_DEADLINE_MARGIN_MS / 1000 / 2],
    ids=["expired", "less-than-margin"],
)
async def test_no_gateway_budget_raises_timeout(
    fake_client: FakeGatewayClient, workspace_id: uuid.UUID, call: str, remaining_s: float
) -> None:
    expired = CallContext(
        workspace_id=workspace_id, request_id="req-2", deadline=time.monotonic() + remaining_s
    )

    with pytest.raises(GatewayTimeoutError) as exc_info:
        await _call(fake_client, call, expired)

    assert exc_info.value.retryable is True
    assert exc_info.value.request_id == "req-2"


@pytest.mark.parametrize("call", ["chat", "embed"])
async def test_cancelled_call_propagates_and_records_nothing(
    fake_client: FakeGatewayClient, call_context: CallContext, call: str
) -> None:
    # A task cancelled before its first step never runs: the caller sees CancelledError
    # and the fake must not report a request that was never sent.
    task = asyncio.create_task(_call(fake_client, call, call_context))
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake_client.calls == []


async def test_cancelling_queued_chat_allows_queued_embed_to_finish(
    fake_client: FakeGatewayClient, call_context: CallContext
) -> None:
    # Neither task has started when chat is cancelled, so this covers queued calls only.
    # In-flight cancellation needs a real I/O wait: it belongs to the HTTP client tests.
    cancelled = asyncio.create_task(_call(fake_client, "chat", call_context))
    kept = asyncio.create_task(_call(fake_client, "embed", call_context))
    cancelled.cancel()

    results = await asyncio.gather(cancelled, kept, return_exceptions=True)

    assert isinstance(results[0], asyncio.CancelledError)
    assert isinstance(results[1], EmbeddingResult)
    assert [type(r) for r in fake_client.calls] == [EmbedRequest]


def test_fake_rejects_non_positive_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        FakeGatewayClient(dimensions=0)
