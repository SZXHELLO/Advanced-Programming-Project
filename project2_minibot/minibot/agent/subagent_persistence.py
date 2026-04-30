"""Disk-backed store for /addagent subagents (survive process restarts)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from loguru import logger

PERSIST_VERSION = 1


def default_store_path(workspace: Path) -> Path:
    return workspace / ".minibot" / "persistent_subagents.json"


class SubagentPersistence:
    """JSON file: list of subagent records (one file per workspace)."""

    def __init__(self, workspace: Path) -> None:
        self._path = default_store_path(workspace)
        self._lock = threading.RLock()

    def ensure_store_file(self) -> None:
        """Create an empty on-disk store if missing so agents can read_file the path.

        Models sometimes guess wrong filenames (e.g. ``subagent_tasks.json``). The
        canonical file is :func:`default_store_path` — ``persistent_subagents.json``.
        """
        with self._lock:
            if self._path.is_file():
                return
            self._atomic_write({"version": PERSIST_VERSION, "records": []})

    @property
    def path(self) -> Path:
        return self._path

    def load_records(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._path.is_file():
                return []
            try:
                raw = self._path.read_text(encoding="utf-8")
            except OSError as e:
                logger.warning("Failed to read subagent store {}: {}", self._path, e)
                return []
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning("Invalid JSON in subagent store {}: {}", self._path, e)
                return []
            if not isinstance(data, dict):
                return []
            if data.get("version") != PERSIST_VERSION:
                return []
            recs = data.get("records")
            if not isinstance(recs, list):
                return []
            return [r for r in recs if isinstance(r, dict)]

    def _atomic_write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp: str | None = None
        try:
            fd, tmp = tempfile.mkstemp(
                dir=self._path.parent, prefix="subagents.", suffix=".tmp"
            )
            with open(fd, "w", encoding="utf-8", closefd=True) as osf:
                osf.write(text)
                osf.flush()
                os.fsync(osf.fileno())
            if tmp is not None:
                os.replace(tmp, self._path)
        except OSError as e:
            logger.warning("Failed to write subagent store {}: {}", self._path, e)
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise

    def save_records(self, records: list[dict[str, Any]]) -> None:
        with self._lock:
            self._atomic_write({"version": PERSIST_VERSION, "records": records})

    def replace_all(self, mutator: Any) -> list[dict[str, Any]]:
        """Load, apply mutator(records) -> new list, save. Returns new list."""
        with self._lock:
            current: list[dict[str, Any]] = []
            if self._path.is_file():
                try:
                    data = json.loads(self._path.read_text(encoding="utf-8"))
                    if (
                        isinstance(data, dict)
                        and data.get("version") == PERSIST_VERSION
                        and isinstance(data.get("records"), list)
                    ):
                        current = [r for r in data["records"] if isinstance(r, dict)]
                except (OSError, json.JSONDecodeError):
                    current = []
            out = mutator(current)
            if not isinstance(out, list):
                out = []
            self._atomic_write({"version": PERSIST_VERSION, "records": out})
            return out

    def upsert(self, record: dict[str, Any]) -> None:
        rid = record.get("id")
        if not rid:
            return

        def m(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in recs if r.get("id") != rid] + [record]

        self.replace_all(m)

    def remove_ids(self, ids: set[str]) -> int:
        if not ids:
            return 0
        removed = 0

        def m(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal removed
            out = [r for r in recs if r.get("id") not in ids]
            removed = len(recs) - len(out)
            return out

        self.replace_all(m)
        return removed
