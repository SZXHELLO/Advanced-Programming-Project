"""Standby registration and start_persisted flows for multi-agent collaboration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from minibot.agent.subagent import SubagentManager
from minibot.bus.queue import MessageBus


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


def test_register_standby_persists_does_not_schedule_task(mgr: SubagentManager, tmp_path) -> None:
    out = mgr.register_standby(
        task="duty text",
        label="alpha",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
    )
    assert "standby" in out.lower() or "registered" in out.lower()
    assert not mgr._running_tasks
    recs = mgr._persistence.load_records()
    assert len(recs) == 1
    assert recs[0]["status"] == "standby"
    assert recs[0]["task"] == "duty text"
    assert recs[0]["label"] == "alpha"


@pytest.mark.asyncio
async def test_start_persisted_schedules_run(mgr: SubagentManager, monkeypatch) -> None:
    mgr.register_standby(
        task="collect news",
        label="news",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
    )
    mock_run = AsyncMock()
    monkeypatch.setattr(mgr, "_run_subagent", mock_run)

    msg = await mgr.start_persisted("cli:direct", label="news", instruction="do it now")
    assert "started" in msg.lower()
    await asyncio.sleep(0)
    assert mock_run.await_count >= 1
    recs = mgr._persistence.load_records()
    assert recs[0]["status"] == "running"


@pytest.mark.asyncio
async def test_start_persisted_by_task_id(mgr: SubagentManager, monkeypatch) -> None:
    mgr.register_standby(
        task="x",
        label="b",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
    )
    tid = mgr._persistence.load_records()[0]["id"]
    mock_run = AsyncMock()
    monkeypatch.setattr(mgr, "_run_subagent", mock_run)
    msg = await mgr.start_persisted("cli:direct", task_id=str(tid))
    assert "started" in msg.lower()
    await asyncio.sleep(0)
    assert mock_run.await_count >= 1


@pytest.mark.asyncio
async def test_start_persisted_allows_completed_status(mgr: SubagentManager, monkeypatch) -> None:
    mgr.register_standby(
        task="collect weather",
        label="weather",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
    )
    rec = mgr._persistence.load_records()[0]
    mgr._persistence.upsert({**rec, "status": "completed"})
    mock_run = AsyncMock()
    monkeypatch.setattr(mgr, "_run_subagent", mock_run)

    msg = await mgr.start_persisted("cli:direct", label="weather")
    assert "started" in msg.lower()
    await asyncio.sleep(0)
    assert mock_run.await_count >= 1
    recs = mgr._persistence.load_records()
    assert recs[0]["status"] == "running"


def test_register_standby_reuses_label_when_not_running(mgr: SubagentManager) -> None:
    mgr.register_standby(
        task="v1",
        label="same",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
    )
    tid = mgr._persistence.load_records()[0]["id"]
    out = mgr.register_standby(
        task="v2",
        label="same",
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
    )
    assert tid in out
    assert len(mgr._persistence.load_records()) == 1
    assert mgr._persistence.load_records()[0]["task"] == "v2"
