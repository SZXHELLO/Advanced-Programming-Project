"""Tests for text ReAct parsing and helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from minibot.agent.tools.base import Tool
from minibot.agent.tools.registry import ToolRegistry
from minibot.providers.base import LLMResponse

from minibot.agent.react_loop import (
    REACT_TOGGLE_OFF,
    REACT_TOGGLE_OFF_PHRASES,
    REACT_TOGGLE_ON,
    REACT_TOGGLE_ON_PHRASES,
    cycle_title,
    detect_react_toggle,
    detect_subagent_delegation_intent,
    finalize_finish_payload,
    normalize_react_markdown_headers,
    normalize_tool_params,
    parse_react_step,
    preprocess_react_model_output,
)


class TestDetectReactToggle:
    """Intent-based toggle detection that tolerates polite prefixes, typos, etc."""

    def test_exact_canonical_off_phrase_returns_off(self) -> None:
        assert detect_react_toggle(REACT_TOGGLE_OFF) == "off"

    def test_exact_canonical_on_phrase_returns_on(self) -> None:
        assert detect_react_toggle(REACT_TOGGLE_ON) == "on"

    def test_all_canonical_off_phrases_detected(self) -> None:
        for phrase in REACT_TOGGLE_OFF_PHRASES:
            assert detect_react_toggle(phrase) == "off", phrase

    def test_all_canonical_on_phrases_detected(self) -> None:
        for phrase in REACT_TOGGLE_ON_PHRASES:
            assert detect_react_toggle(phrase) == "on", phrase

    def test_polite_prefix_please_is_stripped(self) -> None:
        assert detect_react_toggle("请退出react推理循环模式") == "off"
        assert detect_react_toggle("请开启react推理循环模式") == "on"

    def test_trailing_punctuation_is_stripped(self) -> None:
        assert detect_react_toggle("退出react推理循环模式。") == "off"
        assert detect_react_toggle("退出react推理循环模式！") == "off"

    def test_typo_luli_is_folded_to_tuili(self) -> None:
        assert detect_react_toggle("请退出旅理循环模式") == "off"
        assert detect_react_toggle("退出旅理循环模式") == "off"

    def test_missing_react_keyword_accepted_when_mode_context_clear(self) -> None:
        assert detect_react_toggle("退出推理循环模式") == "off"
        assert detect_react_toggle("关闭推理模式") == "off"
        assert detect_react_toggle("开启推理循环模式") == "on"

    def test_english_variants(self) -> None:
        assert detect_react_toggle("disable react") == "off"
        assert detect_react_toggle("enable react") == "on"
        assert detect_react_toggle("turn off react") == "off"

    def test_returns_none_for_unrelated_messages(self) -> None:
        assert detect_react_toggle("你现在处于什么模式") is None
        assert detect_react_toggle("今天天气怎么样") is None
        assert detect_react_toggle("帮我写一个 React 组件") is None

    def test_returns_none_for_ambiguous_or_long_messages(self) -> None:
        assert detect_react_toggle("") is None
        assert detect_react_toggle(None) is None  # type: ignore[arg-type]
        long_msg = (
            "请帮我写一个关于react推理循环模式的详细说明文档，内容要丰富一些"
        )
        assert detect_react_toggle(long_msg) is None

    def test_returns_none_when_verb_conflict(self) -> None:
        assert detect_react_toggle("开启还是关闭react推理循环模式") is None


class TestDelegationIntent:
    def test_orchestration_zh_with_agent_names(self) -> None:
        msg = "请你指挥调度`news agent`与`writing agent`完成收集并整理新闻的工作"
        assert detect_subagent_delegation_intent(msg) is True

    def test_dispatch_zh(self) -> None:
        assert detect_subagent_delegation_intent("请调度 subagent 分析日志") is True

    def test_unrelated_no_false_positive(self) -> None:
        assert detect_subagent_delegation_intent("今天新闻里提到了 agent 模型") is False


def test_preprocess_converts_dsml_invoke_to_react() -> None:
    dsml = (
        "<｜DSML｜tool_calls>\n"
        "<｜DSML｜invoke name=\"glob\">\n"
        '<｜DSML｜parameter name="pattern" string="true">**/*news*</｜DSML｜parameter>\n'
        '<｜DSML｜parameter name="path" string="true">.</｜DSML｜parameter>\n'
        '<｜DSML｜parameter name="entry_type" string="true">files</｜DSML｜parameter>\n'
        "</｜DSML｜invoke>\n"
        "</｜DSML｜tool_calls>"
    )
    normalized = preprocess_react_model_output(dsml)
    thought, action, inp, align, err = parse_react_step(normalized)
    assert err is None
    assert action == "glob"
    assert inp == {"pattern": "**/*news*", "path": ".", "entry_type": "files"}
    assert align is None


def _wire_react_provider(provider: MagicMock, chat_impl) -> None:
    """run_react_loop calls chat_stream_with_retry; wire both from the same impl."""

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        return await chat_impl(messages=messages, tools=tools, **kwargs)

    async def chat_stream_with_retry(*, messages, tools=None, on_content_delta=None, **kwargs):
        r = await chat_impl(messages=messages, tools=tools, **kwargs)
        if on_content_delta and r.content:
            await on_content_delta(r.content)
        return r

    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_stream_with_retry


def test_format_react_text_indents_continuations() -> None:
    from minibot.utils.react_display import format_react_text

    raw = (
        "第一轮循环\n"
        "Thought: first\n"
        "continuation of thought\n"
        "Action: noop\n"
        "Observation: {}\n"
    )
    out = format_react_text(raw)
    assert "continuation of thought" in out
    assert "    continuation of thought" in out


def test_cycle_title_chinese() -> None:
    assert cycle_title(1) == "第一轮循环"
    assert cycle_title(3) == "第三轮循环"
    assert cycle_title(10) == "第十轮循环"
    assert cycle_title(11) == "第11轮循环"


def test_parse_react_tool_call() -> None:
    text = """Thought: need file
Action: read_file
Observation: {"path": "README.md"}
"""
    thought, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert align is None
    assert thought == "need file"
    assert action == "read_file"
    assert inp == {"path": "README.md"}


def test_parse_react_tool_call_legacy_action_input() -> None:
    text = """Thought: need file
Action: read_file
Action Input: {"path": "README.md"}
"""
    thought, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert align is None
    assert inp == {"path": "README.md"}


def test_parse_react_observation_with_alignment() -> None:
    text = """Thought: read README
Action: read_file
Observation: {"path": "README.md"}
事实对齐：推理要求查看项目说明，与读取 README 一致。
"""
    thought, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert inp == {"path": "README.md"}
    assert align is not None
    assert "事实对齐" in align


def test_parse_react_finish_string() -> None:
    text = """Thought: done
Action: finish
Observation: "hello world"
"""
    thought, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert align is None
    assert action == "finish"
    assert inp == "hello world"
    assert finalize_finish_payload(inp) == "hello world"


def test_parse_react_finish_object() -> None:
    text = """Thought: done
Action: finish
Observation: {"answer": "42"}
"""
    thought, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert align is None
    assert action == "finish"
    assert finalize_finish_payload(inp) == "42"


def test_parse_react_markdown_bold_headers() -> None:
    """Models often wrap labels as **Thought:** — parser must still succeed."""
    text = """**Thought:** need to finish
**Action:** finish
**Observation:** {"answer": "ok"}
"""
    thought, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert thought == "need to finish"
    assert action == "finish"
    assert finalize_finish_payload(inp) == "ok"
    assert align is None


def test_normalize_react_markdown_headers_idempotent() -> None:
    plain = "Thought: a\nAction: b\nObservation: {}\n"
    assert normalize_react_markdown_headers(plain) == plain


def test_parse_react_wrapped_in_json_code_fence() -> None:
    """Models sometimes wrap the whole output as ```json {"Thought":..., "Action":..., "Observation":...}```."""
    text = (
        "```json\n"
        "{\n"
        '  "Thought": "要完成任务",\n'
        '  "Action": "finish",\n'
        '  "Observation": {"answer": "你好，世界"}\n'
        "}\n"
        "```"
    )
    thought, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert thought == "要完成任务"
    assert action == "finish"
    assert finalize_finish_payload(inp) == "你好，世界"


def test_parse_react_json_object_without_code_fence() -> None:
    """Bare {"Thought":..., "Action":..., "Observation":...} JSON objects are also unfolded."""
    text = '{"Thought": "t", "Action": "finish", "Observation": "plain answer"}'
    thought, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert thought == "t"
    assert action == "finish"
    assert finalize_finish_payload(inp) == "plain answer"


def test_parse_react_finish_accepts_plain_text_observation() -> None:
    """Action: finish should accept unquoted natural-language Observation bodies."""
    text = "Thought: t\nAction: finish\nObservation: 这是一个答案\n"
    _, action, inp, align, err = parse_react_step(text)
    assert err is None
    assert action == "finish"
    assert finalize_finish_payload(inp) == "这是一个答案"
    assert align is None


def test_parse_react_naked_plain_text_reports_sentinel_error() -> None:
    """Models that ignore the protocol entirely must trigger a targeted
    correction (so the Thought/Action/Observation structure stays visible to
    the user), not a silent implicit finish."""
    from minibot.agent.react_loop import NAKED_PLAIN_TEXT_ERROR

    text = (
        "当然！这里有一个程序员笑话：\n"
        "为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。"
    )
    thought, action, inp, align, err = parse_react_step(text)
    assert err == NAKED_PLAIN_TEXT_ERROR
    assert action is None
    assert inp is None


def test_parse_react_any_label_does_not_trigger_naked_error() -> None:
    """If the output contains ANY ReAct label (even just `Thought:`) we must
    report the specific missing-piece error, not the naked-plain-text sentinel."""
    from minibot.agent.react_loop import NAKED_PLAIN_TEXT_ERROR

    text = "Thought: thinking out loud without an Action line"
    _, _, _, _, err = parse_react_step(text)
    assert err is not None
    assert err != NAKED_PLAIN_TEXT_ERROR
    assert "Action" in err


def test_is_naked_plain_answer_detection() -> None:
    from minibot.agent.react_loop import _is_naked_plain_answer  # type: ignore

    assert _is_naked_plain_answer("just a joke, no labels")
    assert not _is_naked_plain_answer("Thought: x\nAction: finish\nObservation: y")
    assert not _is_naked_plain_answer("   Thought:   reasoning   ")
    assert not _is_naked_plain_answer("")


def test_parse_missing_observation() -> None:
    text = "Thought: x\nAction: read_file\n"
    _, _, _, _, err = parse_react_step(text)
    assert err is not None
    assert "Observation" in err


def test_parse_invalid_json_tool_call_falls_through_to_normalize() -> None:
    """Non-JSON Observation for a tool call: parse is lenient (string payload);
    normalize_tool_params is responsible for rejecting it downstream."""
    text = """Thought: x
Action: read_file
Observation: not-json
"""
    _, action, payload, _, err = parse_react_step(text)
    assert err is None
    assert action == "read_file"
    assert payload == "not-json"
    assert isinstance(normalize_tool_params(payload), str)


def test_normalize_tool_params() -> None:
    assert normalize_tool_params({"a": 1}) == {"a": 1}
    err = normalize_tool_params("oops")
    assert isinstance(err, str) and "object" in err


def test_toggle_constants() -> None:
    assert REACT_TOGGLE_ON == "开启react推理循环模式"
    assert REACT_TOGGLE_OFF == "关闭react推理循环模式"
    assert "开启react推理循环模式" in REACT_TOGGLE_ON_PHRASES
    assert "打开react推理循环模式" in REACT_TOGGLE_ON_PHRASES
    assert "开启react循环推理模式" in REACT_TOGGLE_ON_PHRASES
    assert "打开react循环推理模式" in REACT_TOGGLE_ON_PHRASES
    assert "关闭react推理循环模式" in REACT_TOGGLE_OFF_PHRASES
    assert "退出react推理循环模式" in REACT_TOGGLE_OFF_PHRASES
    assert "关闭react循环推理模式" in REACT_TOGGLE_OFF_PHRASES
    assert "退出react循环推理模式" in REACT_TOGGLE_OFF_PHRASES
    assert "开启react模式" in REACT_TOGGLE_ON_PHRASES
    assert "关闭react模式" in REACT_TOGGLE_OFF_PHRASES


class _NoopTool(Tool):
    @property
    def name(self) -> str:
        return "noop"

    @property
    def description(self) -> str:
        return "noop"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return "observed"


class _FakeSpawnTool(Tool):
    """Stand-in for spawn in delegation / DSML upgrade tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return "fake spawn"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "label": {"type": "string"},
            },
        }

    async def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return "Subagent started (fake)"


class _FakeWriteFileTool(Tool):
    """Stand-in for the real write_file tool in ReAct guardrail tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "fake write_file"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return f"Successfully wrote {len(kwargs.get('content', ''))} characters to {kwargs.get('path')}"


@pytest.mark.asyncio
async def test_run_react_loop_finish() -> None:
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()

    async def chat_impl(*, messages, tools=None, **kwargs):
        assert tools is None
        return LLMResponse(
            content='Thought: t\nAction: finish\nObservation: "ok"',
            tool_calls=[],
            usage={"prompt_tokens": 1, "completion_tokens": 2},
        )

    _wire_react_provider(provider, chat_impl)

    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=3,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "ok"
    assert result.react_trace and "最终答案" in result.react_trace
    assert result.usage.get("prompt_tokens", 0) >= 1


@pytest.mark.asyncio
async def test_run_react_loop_tool_then_finish() -> None:
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        assert tools is None
        n["c"] += 1
        if n["c"] == 1:
            return LLMResponse(
                content="Thought: call\nAction: noop\nObservation: {}",
                tool_calls=[],
                usage={},
            )
        return LLMResponse(
            content='Thought: end\nAction: finish\nObservation: "done"',
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)

    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=5,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "done"
    assert result.react_trace and "第一轮循环" in result.react_trace
    assert result.tools_used == ["noop"]


@pytest.mark.asyncio
async def test_run_react_loop_emits_round_progress_by_default() -> None:
    """Each round calls progress; final_content is still answer-only on finish."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    progress_calls: list[str] = []

    async def chat_impl(*, messages, tools=None, **kwargs):
        return LLMResponse(
            content='Thought: t\nAction: finish\nObservation: {"answer": "457"}',
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)

    async def on_progress(content: str, *, tool_hint: bool = False) -> None:
        progress_calls.append(content)

    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "345+112=?"}],
        model="m",
        max_iterations=3,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
        progress_callback=on_progress,
    )
    assert result.final_content == "457"
    assert len(progress_calls) >= 1
    assert "第一轮循环" in progress_calls[0]


@pytest.mark.asyncio
async def test_run_react_loop_emits_alignment_progress_after_round() -> None:
    """Observation line 1 is JSON; following lines are 事实对齐 — progress emits a separate 事实对齐: line."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    progress_calls: list[str] = []

    async def chat_impl(*, messages, tools=None, **kwargs):
        return LLMResponse(
            content=(
                "Thought: t\n"
                "Action: finish\n"
                'Observation: {"answer": "ok"}\n'
                "Aligning reasoning with the planned finish.\n"
            ),
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)

    async def on_progress(content: str, *, tool_hint: bool = False) -> None:
        progress_calls.append(content)

    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=3,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
        progress_callback=on_progress,
    )
    assert result.final_content == "ok"
    assert any("事实对齐:" in c for c in progress_calls)
    assert any("Aligning reasoning" in c for c in progress_calls)


@pytest.mark.asyncio
async def test_run_react_loop_emit_round_progress_false_silences_progress() -> None:
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    progress_calls: list[str] = []

    async def chat_impl(*, messages, tools=None, **kwargs):
        return LLMResponse(
            content='Thought: t\nAction: finish\nObservation: "x"',
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)

    async def on_progress(content: str, *, tool_hint: bool = False) -> None:
        progress_calls.append(content)

    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=3,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
        progress_callback=on_progress,
        emit_round_progress=False,
    )
    assert result.final_content == "x"
    assert progress_calls == []


@pytest.mark.asyncio
async def test_run_react_loop_on_stream_delta_gets_round_title_and_stream_chunks() -> None:
    """Round title is prefixed to the first streamed chunk (not a separate pre-LLM delta)."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    full = 'Thought: t\nAction: finish\nObservation: "done"'

    async def chat_impl(*, messages, tools=None, **kwargs):
        return LLMResponse(content=full, tool_calls=[], usage={})

    async def chat_stream_with_retry(*, messages, tools=None, on_content_delta=None, **kwargs):
        r = await chat_impl(messages=messages, tools=tools, **kwargs)
        if on_content_delta and r.content:
            await on_content_delta(r.content[:10])
            await on_content_delta(r.content[10:])
        return r

    provider.chat_stream_with_retry = chat_stream_with_retry

    parts: list[str] = []

    async def on_stream_delta(s: str) -> None:
        parts.append(s)

    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=3,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
        on_stream_delta=on_stream_delta,
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "done"
    joined = "".join(parts)
    assert joined == "第一轮循环\n" + full


@pytest.mark.asyncio
async def test_run_react_loop_naked_text_retry_then_complies() -> None:
    """Round 1 naked text → correction Observation sent; round 2 model complies
    with proper Thought/Action/Observation. The three-section structure is
    preserved in the transcript (not silently swallowed)."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        if n["c"] == 1:
            return LLMResponse(
                content="这是一个程序员笑话：为什么程序员分不清万圣节和圣诞节？",
                tool_calls=[],
                usage={},
            )
        return LLMResponse(
            content='Thought: 直接回答\nAction: finish\nObservation: "因为 Oct 31 == Dec 25"',
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "讲个笑话"}],
        model="m",
        max_iterations=5,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "因为 Oct 31 == Dec 25"
    assert n["c"] == 2
    assert result.react_trace is not None
    assert "ReAct 三段协议" in result.react_trace
    assert "Action: finish" in result.react_trace


@pytest.mark.asyncio
async def test_run_react_loop_delegation_intent_does_not_implicit_finish_on_naked_text() -> None:
    """When user explicitly asks for spawn/subagent collaboration, two naked-text
    rounds must NOT trigger implicit finish. The loop should keep correcting
    until a compliant ReAct step arrives."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        if n["c"] == 1:
            return LLMResponse(content="先给一个普通段落，不含ReAct标签", tool_calls=[], usage={})
        if n["c"] == 2:
            return LLMResponse(content="第二次依然不按协议输出", tool_calls=[], usage={})
        return LLMResponse(
            content='Thought: 需要先委派\nAction: finish\nObservation: "已按协议输出"',
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "请用spawn做多agent协作，安排子agent分工"}],
        model="m",
        max_iterations=5,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "已按协议输出"
    assert n["c"] == 3


@pytest.mark.asyncio
async def test_run_react_loop_naked_completed_plain_answer_finishes_immediately() -> None:
    """A naked plain-text completion with artifact path should stop immediately.

    Regression: avoid extra retry rounds that leave CLI in a lingering
    "thinking" state even though the model already gave a concrete file result.
    """
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        return LLMResponse(
            content=(
                "天津旅游攻略.pptx 已经在上次生成好了，文件在工作区根目录：\n"
                "C:\\Users\\25283\\.minibot\\workspace\\天津旅游攻略.pptx"
            ),
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "你把ppt保存在哪里"}],
        model="m",
        max_iterations=5,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert "天津旅游攻略.pptx" in (result.final_content or "")
    assert n["c"] == 1


@pytest.mark.asyncio
async def test_run_react_loop_delegation_upgrades_round1_glob_to_spawn() -> None:
    """DSML glob on round 1 + delegation intent should run spawn with the user task."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    n = {"c": 0}
    dsml = (
        "<｜DSML｜tool_calls>\n"
        "<｜DSML｜invoke name=\"glob\">\n"
        '<｜DSML｜parameter name="pattern" string="true">*.md</｜DSML｜parameter>\n'
        "</｜DSML｜invoke>\n"
        "</｜DSML｜tool_calls>"
    )

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        if n["c"] == 1:
            return LLMResponse(content=dsml, tool_calls=[], usage={})
        return LLMResponse(
            content='Thought: done\nAction: finish\nObservation: "ok"',
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    st = _FakeSpawnTool()
    reg.register(st)

    user = "请你指挥调度`news agent`完成新闻收集"
    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": user}],
        model="m",
        max_iterations=5,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "ok"
    assert st.calls
    assert "news" in (st.calls[0].get("task") or "")


def test_build_react_appendix_requires_tool_for_state_changing_tasks() -> None:
    """Regression guard: the appendix must make it unambiguous that write/
    edit/delete/exec-style requests are category (B) and must invoke the
    matching tool — not ``Action: finish`` with a shell one-liner or a
    fabricated "no permission" excuse."""
    from minibot.agent.react_loop import build_react_appendix

    reg = ToolRegistry()
    reg.register(_NoopTool())
    appendix = build_react_appendix(reg)

    # Category B (side-effectful) guidance must exist.
    assert "state-changing" in appendix or "state changing" in appendix.lower() or "side-effect" in appendix
    assert "write_file" in appendix
    # Explicit prohibition of the two failure modes observed in the wild.
    assert "lack permission" in appendix or "cannot create files" in appendix
    # Shell-one-liner-as-answer prohibition.
    assert "shell one-liner" in appendix or "one-liner" in appendix
    # And the positive example for a write-file task.
    assert "测试2.txt" in appendix or "write_file" in appendix


def test_xml_react_action_maps_to_line_protocol() -> None:
    xml = (
        "<thought>写入文件</thought>\n"
        '<action>write_file(path="a.txt", content="x")</action>'
    )
    out = preprocess_react_model_output(xml)
    _, action, inp, _, err = parse_react_step(out)
    assert err is None
    assert action == "write_file"
    assert inp == {"path": "a.txt", "content": "x"}


def test_xml_react_final_answer_finishes() -> None:
    xml = "<thought>完成</thought>\n<final_answer>hello</final_answer>"
    out = preprocess_react_model_output(xml)
    _, action, inp, _, err = parse_react_step(out)
    assert err is None
    assert action == "finish"
    assert finalize_finish_payload(inp) == "hello"


def test_xml_react_action_write_file_multiline_content_lenient_parse() -> None:
    xml = (
        "<thought>写入多行文件</thought>\n"
        "<action>write_file(path=\"C:/tmp/index.html\", content=\"<!DOCTYPE html>\n"
        "<html>\n<body>ok</body>\n</html>\")</action>"
    )
    out = preprocess_react_model_output(xml)
    _, action, inp, _, err = parse_react_step(out)
    assert err is None
    assert action == "write_file"
    assert isinstance(inp, dict)
    assert inp.get("path") == "C:/tmp/index.html"
    assert "<html>" in str(inp.get("content", ""))


def test_line_protocol_still_works_when_no_xml_tags() -> None:
    text = 'Thought: t\nAction: finish\nObservation: "ok"'
    out = preprocess_react_model_output(text)
    _, action, _, _, err = parse_react_step(out)
    assert err is None
    assert action == "finish"


def test_parse_react_final_answer_alias_accepted() -> None:
    """Original ReAct paper uses ``Final Answer:`` as the terminator; many LLMs
    emit ``Action: final_answer`` (or ``final``/``answer``/``done``). These
    must all normalize to the canonical ``finish`` action."""
    for alias in ("final_answer", "Final_Answer", "final answer", "Final", "answer", "done", "FINISH"):
        text = f'Thought: t\nAction: {alias}\nObservation: "ok"'
        _, action, inp, _, err = parse_react_step(text)
        assert err is None, (alias, err)
        assert action == "finish", (alias, action)
        assert finalize_finish_payload(inp) == "ok"


def test_is_finish_action_helper() -> None:
    from minibot.agent.react_loop import _is_finish_action  # type: ignore

    assert _is_finish_action("finish")
    assert _is_finish_action("Final_Answer")
    assert _is_finish_action("final answer")
    assert _is_finish_action("  DONE  ")
    assert _is_finish_action("terminate")
    assert not _is_finish_action("read_file")
    assert not _is_finish_action("")
    assert not _is_finish_action("finishing_move")


@pytest.mark.asyncio
async def test_run_react_loop_terminates_on_consecutive_parse_errors() -> None:
    """If the model produces unparseable output for MAX_CONSECUTIVE_PARSE_ERRORS
    rounds in a row, the loop terminates early with parse_error_limit rather
    than burning through max_iterations."""
    from minibot.agent.react_loop import MAX_CONSECUTIVE_PARSE_ERRORS, run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        # Every reply has a Thought: label (so this isn't the naked-text path)
        # but is missing Action: → parse error every round.
        return LLMResponse(content=f"Thought: round {n['c']} but no action line", tool_calls=[], usage={})

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=20,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "parse_error_limit"
    assert n["c"] == MAX_CONSECUTIVE_PARSE_ERRORS
    assert result.error is not None
    assert "连续" in (result.final_content or "")


@pytest.mark.asyncio
async def test_run_react_loop_terminates_on_consecutive_tool_errors() -> None:
    """If the same tool keeps failing, the loop terminates with tool_error_limit."""
    from minibot.agent.react_loop import MAX_CONSECUTIVE_TOOL_ERRORS, run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        # Use a nonexistent tool name — registry.execute raises → tool error.
        return LLMResponse(
            content='Thought: try again\nAction: nonexistent_tool\nObservation: {"x": 1}',
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=20,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "tool_error_limit"
    assert n["c"] == MAX_CONSECUTIVE_TOOL_ERRORS
    assert "nonexistent_tool" in (result.final_content or "")


@pytest.mark.asyncio
async def test_run_react_loop_max_iterations_returns_concise_notice() -> None:
    """max_iterations fallback: final_content is the concise termination
    notice only; the full transcript lives in react_trace."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()

    async def chat_impl(*, messages, tools=None, **kwargs):
        # Each round produces a valid tool-call step that loops forever
        # (calls noop, gets an observation, calls noop again, ...).
        return LLMResponse(
            content="Thought: keep going\nAction: noop\nObservation: {}",
            tool_calls=[],
            usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=3,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "max_iterations"
    assert result.final_content is not None
    assert "达到最大循环次数" in result.final_content
    assert "3" in result.final_content
    # The full transcript should be in react_trace (contains round titles).
    assert result.react_trace is not None
    assert "第一轮循环" in result.react_trace
    assert "第三轮循环" in result.react_trace
    # But final_content must NOT duplicate the transcript.
    assert "第一轮循环" not in result.final_content


@pytest.mark.asyncio
async def test_run_react_loop_tool_error_streak_resets_on_different_tool() -> None:
    """Legitimate multi-round tool sequences (search → fetch → …) must not
    trigger the tool-error cap when individual calls succeed or the model
    switches tools. Here the first call fails, the second succeeds, so the
    streak resets and the loop completes normally on finish."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        if n["c"] == 1:
            return LLMResponse(
                content='Thought: try bad tool\nAction: bad_tool\nObservation: {}',
                tool_calls=[], usage={},
            )
        if n["c"] == 2:
            return LLMResponse(
                content='Thought: switch to noop\nAction: noop\nObservation: {}',
                tool_calls=[], usage={},
            )
        return LLMResponse(
            content='Thought: done\nAction: finish\nObservation: "ok"',
            tool_calls=[], usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=10,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "ok"


@pytest.mark.asyncio
async def test_run_react_loop_naked_text_twice_falls_back_to_implicit_finish() -> None:
    """If the model refuses the protocol twice in a row, synthesize a three-
    section finish from the last naked text so the user isn't stuck."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        if n["c"] == 1:
            return LLMResponse(content="first naked answer", tool_calls=[], usage={})
        return LLMResponse(content="still naked, the final one", tool_calls=[], usage={})

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_NoopTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "q"}],
        model="m",
        max_iterations=10,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "still naked, the final one"
    assert n["c"] == 2
    assert result.react_trace is not None
    assert "Action: finish" in result.react_trace


class TestDetectFileStateChangeIntent:
    """Heuristic that distinguishes ``新建 foo.txt`` (B) from ``修改一下语气`` (A)."""

    def test_zh_create_file_is_state_change(self) -> None:
        from minibot.agent.react_loop import detect_file_state_change_intent

        assert detect_file_state_change_intent(
            "请在workspace中新建一个\"测试2.txt\"文件，并向该文件写入\"第二次写入测试\""
        )

    def test_zh_delete_file_is_state_change(self) -> None:
        from minibot.agent.react_loop import detect_file_state_change_intent

        assert detect_file_state_change_intent("请删除 workspace 下的 test.txt 文件")
        assert detect_file_state_change_intent("把上面那个 a.md 文件移除")

    def test_zh_edit_file_is_state_change(self) -> None:
        from minibot.agent.react_loop import detect_file_state_change_intent

        assert detect_file_state_change_intent("修改 README.md 里的第一段")

    def test_en_write_file_is_state_change(self) -> None:
        from minibot.agent.react_loop import detect_file_state_change_intent

        assert detect_file_state_change_intent("please write hello to notes.txt")
        assert detect_file_state_change_intent("create a new file called todo.md")

    def test_extension_hint_alone_triggers_when_verb_present(self) -> None:
        from minibot.agent.react_loop import detect_file_state_change_intent

        assert detect_file_state_change_intent("新建 a.py，写入 print(1)")

    def test_benign_messages_do_not_trigger(self) -> None:
        from minibot.agent.react_loop import detect_file_state_change_intent

        assert not detect_file_state_change_intent("讲个笑话")
        assert not detect_file_state_change_intent("帮我写一个冒泡排序的 Python 函数")
        assert not detect_file_state_change_intent("请你修改一下你的回复语气")
        assert not detect_file_state_change_intent("")


class TestDeleteOnlyIntent:
    """``_is_delete_only_intent`` routes deletion requests to ``delete_file``
    rather than forcing a detour through ``exec`` (which on Windows corrupts
    CJK filenames via cmd.exe's ANSI code page)."""

    def test_zh_pure_delete_is_delete_only(self) -> None:
        from minibot.agent.react_loop import _is_delete_only_intent

        assert _is_delete_only_intent("请删除 测试2.txt")
        assert _is_delete_only_intent("把 workspace 下的 a.log 移除掉")
        assert _is_delete_only_intent("清空 tmp 目录")

    def test_en_pure_delete_is_delete_only(self) -> None:
        from minibot.agent.react_loop import _is_delete_only_intent

        assert _is_delete_only_intent("please delete notes.txt")
        assert _is_delete_only_intent("remove the old log file")

    def test_create_plus_delete_is_not_delete_only(self) -> None:
        from minibot.agent.react_loop import _is_delete_only_intent

        assert not _is_delete_only_intent("新建 a.txt 然后删除 b.txt")
        assert not _is_delete_only_intent("create foo.txt and delete bar.txt")

    def test_no_verb_not_delete_only(self) -> None:
        from minibot.agent.react_loop import _is_delete_only_intent

        assert not _is_delete_only_intent("讲个笑话")
        assert not _is_delete_only_intent("")


@pytest.mark.asyncio
async def test_run_react_loop_rejects_hallucinated_file_finish() -> None:
    """Regression guard: on round 1 the model ``Action: finish``es with a
    fabricated 『文件已创建』 Observation and never calls write_file. The
    guardrail must reject this finish, feed a correction Observation, and on
    round 2 the model (in this mock) actually invokes write_file and then
    finishes legitimately on round 3."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        if n["c"] == 1:
            return LLMResponse(
                content=(
                    "Thought: 需要创建文件并写入指定内容\n"
                    "Action: finish\n"
                    'Observation: "文件 \\"测试2.txt\\" 已创建，内容为：\\"第二次写入测试\\""'
                ),
                tool_calls=[], usage={},
            )
        if n["c"] == 2:
            return LLMResponse(
                content=(
                    "Thought: 真正调用 write_file\n"
                    "Action: write_file\n"
                    'Observation: {"path": "测试2.txt", "content": "第二次写入测试"}'
                ),
                tool_calls=[], usage={},
            )
        return LLMResponse(
            content=(
                "Thought: 写入已成功\n"
                "Action: finish\n"
                'Observation: "已在 workspace 新建 测试2.txt 并写入『第二次写入测试』。"'
            ),
            tool_calls=[], usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    write_tool = _FakeWriteFileTool()
    reg.register(write_tool)

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[
            {"role": "user", "content": "请在workspace中新建一个\"测试2.txt\"文件，并向该文件写入\"第二次写入测试\""}
        ],
        model="m",
        max_iterations=10,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert "测试2.txt" in (result.final_content or "")
    assert n["c"] == 3
    assert result.tools_used == ["write_file"]
    assert len(write_tool.calls) == 1
    assert write_tool.calls[0]["path"] == "测试2.txt"
    assert write_tool.calls[0]["content"] == "第二次写入测试"
    assert result.react_trace is not None
    assert "幻觉" in result.react_trace


@pytest.mark.asyncio
async def test_run_react_loop_hallucinated_finish_gives_up_after_cap() -> None:
    """If the model stubbornly keeps faking a finish after being corrected
    MAX_STATE_CHANGE_REJECTIONS times, the loop accepts the finish rather
    than looping forever (so the user isn't stuck)."""
    from minibot.agent.react_loop import MAX_STATE_CHANGE_REJECTIONS, run_react_loop

    provider = MagicMock()
    n = {"c": 0}

    async def chat_impl(*, messages, tools=None, **kwargs):
        n["c"] += 1
        return LLMResponse(
            content=(
                "Thought: 直接声称成功\n"
                "Action: finish\n"
                'Observation: "文件已创建"'
            ),
            tool_calls=[], usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_FakeWriteFileTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[
            {"role": "user", "content": "请在 workspace 新建 a.txt"}
        ],
        model="m",
        max_iterations=10,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "文件已创建"
    assert n["c"] == MAX_STATE_CHANGE_REJECTIONS + 1


@pytest.mark.asyncio
async def test_run_react_loop_knowledge_question_not_affected_by_guardrail() -> None:
    """Category-A intent (joke, greeting, …) must still be allowed to finish
    in one round — the guardrail only fires on file-state-change intent."""
    from minibot.agent.react_loop import run_react_loop

    provider = MagicMock()

    async def chat_impl(*, messages, tools=None, **kwargs):
        return LLMResponse(
            content='Thought: t\nAction: finish\nObservation: "一个笑话"',
            tool_calls=[], usage={},
        )

    _wire_react_provider(provider, chat_impl)
    reg = ToolRegistry()
    reg.register(_FakeWriteFileTool())

    result = await run_react_loop(
        provider=provider,
        tools=reg,
        initial_messages=[{"role": "user", "content": "讲一个程序员笑话"}],
        model="m",
        max_iterations=5,
        max_tool_result_chars=500,
        provider_retry_mode="standard",
    )
    assert result.stop_reason == "completed"
    assert result.final_content == "一个笑话"
