"""Tests for DeleteFileTool.

Covers the Unicode/CJK filename case that motivated the tool (cmd.exe mangles
Chinese file names when ``exec`` shells out), plus the usual safety concerns:
sandbox escape, refusing to wipe the workspace root, directory handling, and
idempotent cleanup via ``missing_ok``.
"""

from __future__ import annotations

import pytest

from minibot.agent.tools import file_state
from minibot.agent.tools.filesystem import DeleteFileTool


@pytest.fixture()
def tool(tmp_path):
    return DeleteFileTool(workspace=tmp_path, allowed_dir=tmp_path)


@pytest.fixture(autouse=True)
def _clear_file_state():
    file_state.clear()
    yield
    file_state.clear()


class TestDeleteFileBasic:

    @pytest.mark.asyncio
    async def test_delete_existing_file(self, tool, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hi", encoding="utf-8")
        result = await tool.execute(path="a.txt")
        assert "Successfully deleted file" in result
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_unicode_filename(self, tool, tmp_path):
        """The primary motivation: CJK filenames must delete cleanly.

        ExecTool piping del/rm through cmd.exe loses these characters on
        Chinese-locale Windows (GBK ANSI page). The native tool must not.
        """
        f = tmp_path / "测试2.txt"
        f.write_text("第二次写入测试", encoding="utf-8")
        result = await tool.execute(path="测试2.txt")
        assert "Successfully deleted file" in result
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_absolute_path_inside_workspace(self, tool, tmp_path):
        f = tmp_path / "nested" / "b.log"
        f.parent.mkdir()
        f.write_text("x")
        result = await tool.execute(path=str(f))
        assert "Successfully deleted file" in result
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_missing_file_errors_by_default(self, tool):
        result = await tool.execute(path="nope.txt")
        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_ok_succeeds(self, tool):
        result = await tool.execute(path="nope.txt", missing_ok=True)
        assert "Already absent" in result
        assert "Error" not in result

    @pytest.mark.asyncio
    async def test_missing_path_param(self, tool):
        result = await tool.execute()
        assert "Error" in result


class TestDeleteFileDirectory:

    @pytest.mark.asyncio
    async def test_delete_empty_directory(self, tool, tmp_path):
        d = tmp_path / "empty_dir"
        d.mkdir()
        result = await tool.execute(path="empty_dir")
        assert "Successfully deleted empty directory" in result
        assert not d.exists()

    @pytest.mark.asyncio
    async def test_non_empty_directory_without_recursive_rejects(self, tool, tmp_path):
        d = tmp_path / "full"
        d.mkdir()
        (d / "x.txt").write_text("content")
        result = await tool.execute(path="full")
        assert "Error" in result
        assert "recursive=true" in result
        assert d.exists()  # not deleted

    @pytest.mark.asyncio
    async def test_non_empty_directory_with_recursive(self, tool, tmp_path):
        d = tmp_path / "full"
        d.mkdir()
        (d / "x.txt").write_text("content")
        (d / "sub").mkdir()
        (d / "sub" / "y.txt").write_text("other")
        result = await tool.execute(path="full", recursive=True)
        assert "Successfully deleted directory" in result
        assert "2 files removed" in result
        assert not d.exists()


class TestDeleteFileSafety:

    @pytest.mark.asyncio
    async def test_escape_sandbox_blocked(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        victim = outside / "important.txt"
        victim.write_text("please don't")

        tool = DeleteFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute(path=str(victim))
        assert "Error" in result
        assert "outside" in result.lower()
        assert victim.exists()

    @pytest.mark.asyncio
    async def test_refuse_to_delete_workspace_root(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "keep.txt").write_text("stay")

        tool = DeleteFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute(path=str(workspace))
        assert "Error" in result
        assert "sandbox root" in result.lower()
        assert workspace.exists()

    @pytest.mark.asyncio
    async def test_refuse_to_delete_workspace_root_relative(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        tool = DeleteFileTool(workspace=workspace, allowed_dir=workspace)
        result = await tool.execute(path=".")
        assert "Error" in result
        assert "sandbox root" in result.lower()
        assert workspace.exists()


class TestDeleteFileStateTracking:

    @pytest.mark.asyncio
    async def test_clears_read_state(self, tool, tmp_path):
        f = tmp_path / "tracked.txt"
        f.write_text("hello")
        file_state.record_read(f)
        assert str(f.resolve()) in file_state._state

        result = await tool.execute(path="tracked.txt")
        assert "Successfully deleted" in result
        assert str(f.resolve()) not in file_state._state
