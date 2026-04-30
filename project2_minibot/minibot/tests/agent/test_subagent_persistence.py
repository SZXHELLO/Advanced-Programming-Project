"""Tests for subagent JSON persistence store."""

import json
from pathlib import Path

from minibot.agent.subagent_persistence import SubagentPersistence, default_store_path


def test_ensure_store_file_creates_empty_json(tmp_path: Path) -> None:
    store = SubagentPersistence(tmp_path)
    assert not store.path.is_file()
    store.ensure_store_file()
    assert store.path.is_file()
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data.get("version") == 1
    assert data.get("records") == []


def test_upsert_and_remove(tmp_path: Path) -> None:
    store = SubagentPersistence(tmp_path)
    assert store.path == default_store_path(tmp_path)
    a = {
        "id": "a1b2c3d4",
        "label": "t1",
        "task": "x",
        "session_key": "cli:direct",
        "status": "running",
    }
    store.upsert(a)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(data["records"]) == 1
    store.upsert({**a, "status": "completed"})
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["records"][0]["status"] == "completed"
    n = store.remove_ids({"a1b2c3d4"})
    assert n == 1
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["records"] == []

