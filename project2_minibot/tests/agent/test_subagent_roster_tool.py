"""Tests for subagent_roster tool: session scope, guards, rate limit."""

from __future__ import annotations

import asyncio

import pytest

from minibot.agent.subagent import SubagentManager
from minibot.agent.tools.subagent_roster import SubagentRosterTool
from minibot.bus.queue import MessageBus
from unittest.mock import MagicMock


@pytest.fixture
def mgr(tmp_path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    return SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=4096,
    )


@pytest.mark.asyncio
async def test_list_session_scoped(mgr: SubagentManager) -> None:
    mgr.register_standby("duty-a", "la", "cli", "direct", "cli:direct")
    mgr.register_standby("duty-b", "lb", "cli", "direct", "other:room")
    tool = SubagentRosterTool(mgr)
    tool.set_context("cli", "direct", routing_session_key="cli:direct")
    out = await tool.execute(action="list")
    assert "la" in out
    assert "duty-a" in out
    assert "lb" not in out


@pytest.mark.asyncio
async def test_register_requires_ack_for_create(mgr: SubagentManager) -> None:
    tool = SubagentRosterTool(mgr)
    tool.set_context("cli", "direct", routing_session_key="cli:direct")
    out = await tool.execute(
        action="register",
        label="new1",
        duty="x" * 10,
        acknowledge_create=False,
    )
    assert out.startswith("Error:")
    assert "acknowledge_create" in out


@pytest.mark.asyncio
async def test_register_create_success(mgr: SubagentManager) -> None:
    tool = SubagentRosterTool(mgr)
    tool.set_context("cli", "direct", routing_session_key="cli:direct")
    out = await tool.execute(
        action="register",
        label="new1",
        duty="x" * 10,
        acknowledge_create=True,
    )
    assert not out.startswith("Error:")
    assert len(mgr._persistence.load_records()) == 1


@pytest.mark.asyncio
async def test_register_conflict_requires_update_existing(mgr: SubagentManager) -> None:
    mgr.register_standby("v1", "same", "cli", "direct", "cli:direct")
    tool = SubagentRosterTool(mgr)
    tool.set_context("cli", "direct", routing_session_key="cli:direct")
    out = await tool.execute(
        action="register",
        label="same",
        duty="y" * 10,
        acknowledge_create=True,
        update_existing=False,
    )
    assert out.startswith("Error:")
    assert "update_existing" in out
    assert mgr._persistence.load_records()[0]["task"] == "v1"


@pytest.mark.asyncio
async def test_register_update_with_flag(mgr: SubagentManager) -> None:
    mgr.register_standby("v1", "same", "cli", "direct", "cli:direct")
    tool = SubagentRosterTool(mgr)
    tool.set_context("cli", "direct", routing_session_key="cli:direct")
    out = await tool.execute(
        action="register",
        label="same",
        duty="y" * 10,
        update_existing=True,
    )
    assert not out.startswith("Error:")
    assert mgr._persistence.load_records()[0]["task"] == "y" * 10


@pytest.mark.asyncio
async def test_register_rejects_when_running(mgr: SubagentManager, monkeypatch) -> None:
    mgr.register_standby("duty-here", "runme", "cli", "direct", "cli:direct")
    tid = mgr._persistence.load_records()[0]["id"]
    fake = asyncio.create_task(asyncio.sleep(3600))
    mgr._running_tasks[str(tid)] = fake
    try:
        tool = SubagentRosterTool(mgr)
        tool.set_context("cli", "direct", routing_session_key="cli:direct")
        out = await tool.execute(
            action="register",
            label="runme",
            duty="z" * 10,
            update_existing=True,
        )
        assert "running" in out.lower()
    finally:
        fake.cancel()
        try:
            await fake
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_register_ambiguous_label(mgr: SubagentManager) -> None:
    base = {
        "label": "dup",
        "task": "t",
        "session_key": "cli:direct",
        "origin_channel": "cli",
        "origin_chat_id": "direct",
        "status": "standby",
    }
    mgr._persistence.upsert({**base, "id": "aaaaaaaa"})
    mgr._persistence.upsert({**base, "id": "bbbbbbbb"})
    tool = SubagentRosterTool(mgr)
    tool.set_context("cli", "direct", routing_session_key="cli:direct")
    out = await tool.execute(
        action="register",
        label="dup",
        duty="z" * 10,
        update_existing=True,
    )
    assert out.startswith("Error:")
    assert "multiple" in out.lower() or "disambiguate" in out.lower()


@pytest.mark.asyncio
async def test_register_budget_per_session(mgr: SubagentManager, monkeypatch) -> None:
    import minibot.agent.tools.subagent_roster as roster

    roster._register_windows.clear()
    seq = iter([1000.0 + i * 0.01 for i in range(20)])
    monkeypatch.setattr(roster.time, "monotonic", lambda: next(seq))

    tool = SubagentRosterTool(mgr)
    tool.set_context("cli", "direct", routing_session_key="budget-sk")
    for i in range(3):
        out = await tool.execute(
            action="register",
            label=f"n{i}",
            duty="p" * 10,
            acknowledge_create=True,
        )
        assert not out.startswith("Error:")
    out4 = await tool.execute(
        action="register",
        label="n3",
        duty="p" * 10,
        acknowledge_create=True,
    )
    assert out4.startswith("Error:")
    assert "at most" in out4.lower()
