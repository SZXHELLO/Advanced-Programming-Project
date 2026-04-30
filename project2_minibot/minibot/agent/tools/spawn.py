"""Spawn tool for creating background subagents."""

import re
from typing import TYPE_CHECKING, Any

from minibot.agent.tools.base import Tool, tool_parameters
from minibot.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from minibot.agent.subagent import SubagentManager


_RESPONSIBILITY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "文件分析",
        ("readme", "文档", "文件", ".md", ".txt", "目录", "structure", "analy", "scan files"),
    ),
    (
        "代码审查",
        ("代码", "审查", "review", "lint", "test", "bug", "风险", "refactor", "质量"),
    ),
    (
        "网络查询",
        ("联网", "网络", "搜索", "web", "http", "api", "crawl", "fetch", "benchmark"),
    ),
)


def infer_subagent_responsibilities(task_text: str) -> list[str]:
    """Infer likely subagent responsibilities from task text."""
    txt = (task_text or "").lower()
    roles: list[str] = []
    for role, hints in _RESPONSIBILITY_HINTS:
        if any(h in txt for h in hints):
            roles.append(role)
    return roles or ["通用任务处理"]


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema(
            "Natural-language task for the subagent (preferred). "
            "Use this OR `command` (below), not a fictional shell tool API."
        ),
        label=StringSchema("Optional short label for the task (for display)"),
        command=StringSchema(
            "Optional: shell command the subagent should run in the workspace "
            "(mapped into `task` if `task` is empty)."
        ),
        output_file=StringSchema(
            "Optional path hint to write results to, combined with `command` when `task` is empty."
        ),
        from_persisted_label=StringSchema(
            "If set, start a persisted subagent (from `/addagent`) by this **label** in the "
            "current session instead of inventing a new task. Supports `standby` **and `completed`** "
            "records (completed duties can be re-run). The saved duty is used; optional "
            "`task` is treated as a one-shot coordinator instruction appended to that duty. "
            "You can also pass a 6–8 char hex **id** if the label is ambiguous."
        ),
        required=[],
    )
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"

    def set_context(
        self,
        channel: str,
        chat_id: str,
        *,
        routing_session_key: str | None = None,
    ) -> None:
        """Set the origin context for subagent announcements.

        *routing_session_key* matches AgentLoop's effective session key (e.g.
        ``unified:default``) so subagent handoff shares the same lock as the main turn.
        """
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = routing_session_key or f"{channel}:{chat_id}"

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done. "
            "Pass parameters as JSON: required either `task` (full instructions) or `command` "
            "(with optional `output_file`); do not send only `output_file` or other invented keys. "
            "To run a persisted subagent from `/addagent`, set `from_persisted_label` "
            "to its label (and optionally `task` as extra instructions). "
            "`completed` records are callable and can be re-used. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        base = super().validate_params(params)
        if base:
            return base
        fpl = (
            (params.get("from_persisted_label") or "").strip()
            if isinstance(params.get("from_persisted_label"), str)
            else ""
        )
        task = (params.get("task") or "").strip() if isinstance(params.get("task"), str) else ""
        cmd = (params.get("command") or "").strip() if isinstance(params.get("command"), str) else ""
        if fpl:
            return []
        if not task and not cmd:
            return ["must provide non-empty `task` and/or `command`, or `from_persisted_label`"]
        return []

    async def execute(
        self,
        task: str | None = None,
        label: str | None = None,
        command: str | None = None,
        output_file: str | None = None,
        from_persisted_label: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        fpl = (from_persisted_label or "").strip()
        if fpl:
            if (command or "").strip():
                return (
                    "Error: use either `from_persisted_label` (optional `task` as instruction) "
                    "or `command`, not both."
                )
            inst = (task or "").strip() or None
            msg: str | None = None
            rec = None
            if re.fullmatch(r"[0-9a-fA-F]{6,8}", fpl):
                msg = await self._manager.start_persisted(
                    self._session_key,
                    task_id=fpl,
                    instruction=inst,
                )
                if not msg.startswith("Error:"):
                    rec = self._manager._find_persisted_record(
                        self._session_key, task_id=fpl
                    )
            if msg is None or msg.startswith("Error: no persisted"):
                msg = await self._manager.start_persisted(
                    self._session_key,
                    label=fpl,
                    instruction=inst,
                )
                if not msg.startswith("Error:"):
                    try:
                        rec = self._manager._find_persisted_record(
                            self._session_key, label=fpl
                        )
                    except ValueError:
                        rec = None
            if msg.startswith("Error:"):
                return msg
            duty = str(rec.get("task") or "") if rec else (inst or "")
            roles = infer_subagent_responsibilities(duty or inst or fpl)
            return msg + f"\nSubagent roles: {', '.join(roles)}"

        t = (task or "").strip()
        if not t:
            cmd = (command or "").strip()
            if cmd:
                of = (output_file or "").strip()
                t = f"Run in the workspace: {cmd}" + (f" (write or update: {of})" if of else "")
        if not t:
            return "Error: spawn needs a `task` string or a `command` to delegate."
        roles = infer_subagent_responsibilities(t)
        eff_label = (label or "").strip() or roles[0]
        return await self._manager.spawn(
            task=t,
            label=eff_label,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
        ) + f"\nSubagent roles: {', '.join(roles)}"
