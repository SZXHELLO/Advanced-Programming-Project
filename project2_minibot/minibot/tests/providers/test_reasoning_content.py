"""Tests for reasoning_content extraction in OpenAICompatProvider.

Covers non-streaming (_parse) and streaming (_parse_chunks) paths for
providers that return a reasoning_content field (e.g. MiMo, DeepSeek-R1).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from minibot.providers.openai_compat_provider import OpenAICompatProvider


# ── _parse: non-streaming ─────────────────────────────────────────────────


def test_parse_dict_extracts_reasoning_content() -> None:
    """reasoning_content at message level is surfaced in LLMResponse."""
    with patch("minibot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    response = {
        "choices": [{
            "message": {
                "content": "42",
                "reasoning_content": "Let me think step by step…",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    }

    result = provider._parse(response)

    assert result.content == "42"
    assert result.reasoning_content == "Let me think step by step…"


def test_parse_dict_reasoning_content_none_when_absent() -> None:
    """reasoning_content is None when the response doesn't include it."""
    with patch("minibot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    response = {
        "choices": [{
            "message": {"content": "hello"},
            "finish_reason": "stop",
        }],
    }

    result = provider._parse(response)

    assert result.reasoning_content is None


# ── _parse_chunks: streaming dict branch ─────────────────────────────────


def test_parse_chunks_dict_accumulates_reasoning_content() -> None:
    """reasoning_content deltas in dict chunks are joined into one string."""
    chunks = [
        {
            "choices": [{
                "finish_reason": None,
                "delta": {"content": None, "reasoning_content": "Step 1. "},
            }],
        },
        {
            "choices": [{
                "finish_reason": None,
                "delta": {"content": None, "reasoning_content": "Step 2."},
            }],
        },
        {
            "choices": [{
                "finish_reason": "stop",
                "delta": {"content": "answer"},
            }],
        },
    ]

    result = OpenAICompatProvider._parse_chunks(chunks)

    assert result.content == "answer"
    assert result.reasoning_content == "Step 1. Step 2."


def test_parse_chunks_dict_reasoning_content_none_when_absent() -> None:
    """reasoning_content is None when no chunk contains it."""
    chunks = [
        {"choices": [{"finish_reason": "stop", "delta": {"content": "hi"}}]},
    ]

    result = OpenAICompatProvider._parse_chunks(chunks)

    assert result.content == "hi"
    assert result.reasoning_content is None


# ── _parse_chunks: streaming SDK-object branch ────────────────────────────


def _make_reasoning_chunk(reasoning: str | None, content: str | None, finish: str | None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)
    choice = SimpleNamespace(finish_reason=finish, delta=delta)
    return SimpleNamespace(choices=[choice], usage=None)


def test_parse_chunks_sdk_accumulates_reasoning_content() -> None:
    """reasoning_content on SDK delta objects is joined across chunks."""
    chunks = [
        _make_reasoning_chunk("Think… ", None, None),
        _make_reasoning_chunk("Done.", None, None),
        _make_reasoning_chunk(None, "result", "stop"),
    ]

    result = OpenAICompatProvider._parse_chunks(chunks)

    assert result.content == "result"
    assert result.reasoning_content == "Think… Done."


def test_parse_chunks_sdk_reasoning_content_none_when_absent() -> None:
    """reasoning_content is None when SDK deltas carry no reasoning_content."""
    chunks = [_make_reasoning_chunk(None, "hello", "stop")]

    result = OpenAICompatProvider._parse_chunks(chunks)

    assert result.reasoning_content is None


@pytest.mark.asyncio
async def test_chat_retries_once_after_reasoning_roundtrip_error() -> None:
    """Provider auto-retries with repaired history on reasoning-content errors."""
    with patch("minibot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider()

    class _Err(Exception):
        def __init__(self, body: str):
            super().__init__(body)
            self.body = body

    first_error = _Err("The reasoning_content in the thinking mode must be passed back to the API.")
    good_response = {
        "choices": [{
            "message": {"content": "ok"},
            "finish_reason": "stop",
        }],
    }

    create_mock = AsyncMock(side_effect=[first_error, good_response])
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create_mock),
        )
    )

    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "legacy no reasoning"},
        {"role": "user", "content": "u2"},
    ]
    result = await provider.chat(messages=messages)
    assert result.content == "ok"
    assert create_mock.await_count == 2
    first_messages = create_mock.await_args_list[0].kwargs["messages"]
    assert len(first_messages) == 3
    second_messages = create_mock.await_args_list[1].kwargs["messages"]
    assert all(m.get("role") != "assistant" for m in second_messages)


def test_repair_drops_assistant_with_tool_calls_when_no_reasoning() -> None:
    """Thinking round-trip repair removes tool-calling assistants lacking reasoning_content."""
    messages = [
        {"role": "user", "content": "u1"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_abc12",
                "type": "function",
                "function": {"name": "spawn", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_abc12", "content": "done"},
        {"role": "user", "content": "u2"},
    ]
    repaired = OpenAICompatProvider._repair_messages_for_reasoning_roundtrip(messages)
    assert [m["role"] for m in repaired] == ["user", "user"]
    assert repaired[0]["content"] == "u1"
    assert repaired[1]["content"] == "u2"


def test_is_reasoning_roundtrip_error_text_matches_provider_wording() -> None:
    """Detect error copy that says reasoning must be *passed back* (not only 'passed back')."""
    text = "Error: {'message': 'The reasoning_content in the thinking mode must be passed back to the API.'}"
    assert OpenAICompatProvider._is_reasoning_roundtrip_error_text(text)
