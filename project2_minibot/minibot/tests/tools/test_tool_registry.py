from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from minibot.agent.tools.base import Tool
from minibot.agent.tools.registry import ToolRegistry
from minibot.agent.tools.spawn import SpawnTool, infer_subagent_responsibilities


class _FakeTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return kwargs


def _tool_names(definitions: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for definition in definitions:
        fn = definition.get("function", {})
        names.append(fn.get("name", ""))
    return names


def test_get_definitions_orders_builtins_then_mcp_tools() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("mcp_git_status"))
    registry.register(_FakeTool("write_file"))
    registry.register(_FakeTool("mcp_fs_list"))
    registry.register(_FakeTool("read_file"))

    assert _tool_names(registry.get_definitions()) == [
        "read_file",
        "write_file",
        "mcp_fs_list",
        "mcp_git_status",
    ]


def test_prepare_call_read_file_rejects_non_object_params_with_actionable_hint() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file"))

    tool, params, error = registry.prepare_call("read_file", ["foo.txt"])

    assert tool is None
    assert params == ["foo.txt"]
    assert error is not None
    assert "must be a JSON object" in error
    assert "Use named parameters" in error


def test_copy_excluding_omits_tools() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file"))
    registry.register(_FakeTool("message"))
    slim = registry.copy_excluding({"message"})
    assert "message" not in slim.tool_names
    assert "read_file" in slim.tool_names
    assert len(slim) == 1


def test_prepare_call_other_tools_keep_generic_object_validation() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("grep"))

    tool, params, error = registry.prepare_call("grep", ["TODO"])

    assert tool is not None
    assert params == ["TODO"]
    assert error == "Error: Invalid parameters for tool 'grep': parameters must be an object, got list"


def test_spawn_tool_accepts_task_or_command() -> None:
    mgr = MagicMock()
    st = SpawnTool(mgr)
    assert st.validate_params({}) == ["must provide non-empty `task` and/or `command`"]
    assert st.validate_params({"command": "python a.py"}) == []
    assert st.validate_params({"task": "do work"}) == []


@pytest.mark.asyncio
async def test_spawn_tool_maps_command_to_task() -> None:
    mgr = MagicMock()
    mgr.spawn = AsyncMock(
        return_value="Subagent [x] started (id: abcd). I'll notify you when it completes."
    )
    st = SpawnTool(mgr)
    await st.execute(command="python analyze.py a.md", output_file="风险点.md")
    mgr.spawn.assert_called_once()
    cal = mgr.spawn.call_args
    assert "python analyze.py" in cal.kwargs["task"]
    assert "风险点.md" in cal.kwargs["task"]


def test_registry_prepare_call_spawn_with_command_only() -> None:
    mgr = MagicMock()
    mgr.spawn = AsyncMock(return_value="ok")
    registry = ToolRegistry()
    registry.register(SpawnTool(mgr))
    tool, params, error = registry.prepare_call(
        "spawn", {"command": "echo hi", "output_file": "o.txt"}
    )
    assert error is None
    assert tool is not None
    assert "echo hi" in params.get("command", "")


def test_infer_subagent_responsibilities_from_task_text() -> None:
    roles = infer_subagent_responsibilities("分析README并进行代码review，再web搜索相关项目")
    assert "文件分析" in roles
    assert "代码审查" in roles
    assert "网络查询" in roles
