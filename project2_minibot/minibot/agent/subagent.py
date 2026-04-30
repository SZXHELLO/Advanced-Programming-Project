"""Subagent manager for background task execution."""

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from minibot.agent.subagent_persistence import SubagentPersistence

from minibot.agent.hook import AgentHook, AgentHookContext
from minibot.utils.prompt_templates import render_template
from minibot.agent.runner import AgentRunSpec, AgentRunner
from minibot.agent.skills import BUILTIN_SKILLS_DIR
from minibot.agent.tools.filesystem import (
    DeleteFileTool,
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from minibot.agent.tools.registry import ToolRegistry
from minibot.agent.tools.search import GlobTool, GrepTool
from minibot.agent.tools.shell import ExecTool, merge_subagent_exec_allowed_commands
from minibot.agent.tools.web import WebFetchTool, WebSearchTool
from minibot.bus.events import InboundMessage
from minibot.bus.queue import MessageBus
from minibot.config.schema import ExecToolConfig, WebToolsConfig
from minibot.providers.base import LLMProvider


class _SubagentBusHook(AgentHook):
    """Hook for subagent execution: logs debug info and publishes per-step progress to the bus."""

    def __init__(self, task_id: str, manager: "SubagentManager", label: str, origin: dict[str, str]) -> None:
        super().__init__()
        self._task_id = task_id
        self._manager = manager
        self._label = label
        self._origin = origin

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        from minibot.utils.helpers import strip_think

        # Emit the model's thought text (if present)
        if context.response and context.response.content:
            thought = strip_think(context.response.content).strip()
            if thought:
                await self._manager._publish_subagent_progress(
                    thought,
                    origin=self._origin,
                    task_id=self._task_id,
                    label=self._label,
                    tool_hint=False,
                )

        # Emit each tool call as a hint line
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tc.name, args_str,
            )
            short_args = args_str[:120] + ("..." if len(args_str) > 120 else "")
            hint = f"[tool] {tc.name}({short_args})"
            await self._manager._publish_subagent_progress(
                hint,
                origin=self._origin,
                task_id=self._task_id,
                label=self._label,
                tool_hint=True,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        # Emit the last tool result as an Observation line
        if context.tool_events:
            last = context.tool_events[-1]
            detail = last.get("detail", "")
            if detail:
                obs = "Observation: " + detail[:200] + ("..." if len(detail) > 200 else "")
                await self._manager._publish_subagent_progress(
                    obs,
                    origin=self._origin,
                    task_id=self._task_id,
                    label=self._label,
                    tool_hint=False,
                )


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        web_config: "WebToolsConfig | None" = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
    ):
        from minibot.config.schema import ExecToolConfig

        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.web_config = web_config or WebToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.runner = AgentRunner(provider)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        # task_id -> {"label", "session_key", "started_at_monotonic", "task_text", "persist", ...}
        self._task_meta: dict[str, dict[str, Any]] = {}
        self._persistence = SubagentPersistence(workspace)
        self._persistence.ensure_store_file()
        self._resumed = False
        self._resume_lock = asyncio.Lock()

    @property
    def persistence_path(self) -> Path:
        return self._persistence.path

    def is_task_running(self, task_id: str) -> bool:
        t = self._running_tasks.get(str(task_id))
        return t is not None and not t.done()

    async def ensure_resumed(self) -> None:
        """Re-start background tasks for persisted subagents (running/interrupted) after a restart.

        ``standby`` agents are not started automatically; use ``/runagent`` or ``spawn``
        with ``from_persisted_label``.
        """
        async with self._resume_lock:
            if self._resumed:
                return
            self._resumed = True
        for r in self._persistence.load_records():
            st = (r.get("status") or "").lower()
            if st not in ("running", "interrupted"):
                continue
            rid = r.get("id")
            if not rid or str(rid) in self._running_tasks:
                continue
            try:
                await self.spawn(
                    str(r.get("task") or ""),
                    label=r.get("label"),
                    origin_channel=str(r.get("origin_channel") or "cli"),
                    origin_chat_id=str(r.get("origin_chat_id") or "direct"),
                    session_key=r.get("session_key"),
                    task_id=str(rid),
                    persist=True,
                    resume=True,
                )
            except Exception as e:
                logger.warning("Failed to resume subagent {}: {}", rid, e)

    def _find_persisted_record(
        self,
        session_key: str,
        *,
        label: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return one persisted row for *session_key* or raise ValueError if label is ambiguous."""
        recs = self._persistence.load_records()
        sk = str(session_key)
        if task_id:
            tid = str(task_id).strip()
            for r in recs:
                if str(r.get("session_key") or "") != sk:
                    continue
                if str(r.get("id") or "") == tid:
                    return r
            return None
        if label is not None:
            lab = str(label).strip()
            matches = [
                r
                for r in recs
                if str(r.get("session_key") or "") == sk
                and str(r.get("label") or "").strip() == lab
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                ids = ", ".join(str(m.get("id")) for m in matches)
                raise ValueError(
                    f"multiple subagents match label {lab!r}; disambiguate with id: {ids}"
                )
            return None
        return None

    def register_standby(
        self,
        task: str,
        label: str | None,
        origin_channel: str,
        origin_chat_id: str,
        session_key: str | None,
    ) -> str:
        """Persist a subagent definition with status ``standby``; does not start execution.

        The *task* string is the agent's standing duty/role. Use :meth:`start_persisted`
        or ``spawn(..., from_persisted_label=...)`` to run a turn.
        """
        routing_key = session_key if session_key else f"{origin_channel}:{origin_chat_id}"
        display_label = (
            (label or "").strip()
            or (task[:30] + ("..." if len(task) > 30 else "")).strip()
            or "subagent"
        )
        duty = (task or "").strip()
        if not duty:
            return "Error: duty/task text must be non-empty."

        for r in self._persistence.load_records():
            if str(r.get("session_key") or "") != str(routing_key):
                continue
            if str(r.get("label") or "").strip() != display_label:
                continue
            rid = str(r.get("id") or "")
            if not rid:
                continue
            if self.is_task_running(rid):
                return (
                    f"Error: subagent `{display_label}` is already running (id: {rid}). "
                    "Use /stop or wait before re-registering."
                )
            self._persistence.upsert(
                {
                    **r,
                    "task": duty,
                    "label": display_label,
                    "status": "standby",
                    "origin_channel": origin_channel,
                    "origin_chat_id": origin_chat_id,
                    "session_key": routing_key,
                }
            )
            return (
                f"Subagent `{display_label}` updated (standby, id: {rid}). Duty saved.\n"
                f"Start with `/runagent {display_label}` or optional "
                f"`/runagent {display_label} | <instruction>` — or tool `spawn` with "
                f"`from_persisted_label`."
            )

        tid = str(uuid.uuid4())[:8]
        self._persistence.upsert(
            {
                "id": tid,
                "label": display_label,
                "task": duty,
                "session_key": routing_key,
                "origin_channel": origin_channel,
                "origin_chat_id": origin_chat_id,
                "status": "standby",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return (
            f"Subagent `{display_label}` registered standby (id: {tid}). Duty saved.\n"
            f"Start with `/runagent {display_label}` or `/runagent {display_label} | <instruction>` "
            f"— or tool `spawn` with `from_persisted_label`."
        )

    async def start_persisted(
        self,
        session_key: str,
        *,
        label: str | None = None,
        task_id: str | None = None,
        instruction: str | None = None,
    ) -> str:
        """Start a persisted subagent.

        Callable statuses include ``standby`` and ``completed`` (plus
        ``error`` / ``interrupted`` for retry). This allows the main agent to
        re-use previously completed subagents via ``spawn(from_persisted_label=...)``.
        """
        try:
            rec = self._find_persisted_record(
                session_key, label=label, task_id=task_id
            )
        except ValueError as e:
            return f"Error: {e}"
        if rec is None and label and re.fullmatch(r"[0-9a-fA-F]{6,8}", str(label).strip()):
            rec = self._find_persisted_record(
                session_key, task_id=str(label).strip()
            )
        if not rec:
            who = label or task_id or "?"
            return f"Error: no persisted subagent matched {who!r} in this session."

        tid = str(rec.get("id") or "")
        if not tid:
            return "Error: persisted record has no id."
        if self.is_task_running(tid):
            return (
                f"Error: subagent `{rec.get('label')}` (id: {tid}) is already running."
            )
        status = str(rec.get("status") or "").strip().lower()
        callable_statuses = {"standby", "completed", "error", "interrupted", "running"}
        if status and status not in callable_statuses:
            return (
                f"Error: subagent `{rec.get('label')}` (id: {tid}) has unsupported "
                f"status `{status}` and cannot be started."
            )

        duty = str(rec.get("task") or "").strip()
        display_label = str(rec.get("label") or "subagent").strip()
        origin = {
            "channel": str(rec.get("origin_channel") or "cli"),
            "chat_id": str(rec.get("origin_chat_id") or "direct"),
        }
        routing_key = str(rec.get("session_key") or session_key)
        full_task = duty
        ins = (instruction or "").strip()
        if ins:
            full_task = f"{duty}\n\n---\nCoordinator instruction:\n{ins}"

        now_ts = time.time()
        self._persistence.upsert(
            {
                **rec,
                "status": "running",
                "last_run_started_at_unix": now_ts,
                "task": duty,
            }
        )
        self._schedule_subagent_task(
            tid, full_task, display_label, origin, routing_key, persist=True
        )
        logger.info("Started persisted subagent [{}]: {}", tid, display_label)
        return (
            f"Subagent [{display_label}] started (id: {tid}). I'll notify you when it completes."
        )

    def _schedule_subagent_task(
        self,
        task_id: str,
        full_task: str,
        display_label: str,
        origin: dict[str, str],
        routing_key: str,
        *,
        persist: bool,
    ) -> None:
        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id,
                full_task,
                display_label,
                origin,
                routing_key,
                persist=persist,
            )
        )
        self._running_tasks[task_id] = bg_task
        self._task_meta[task_id] = {
            "label": display_label,
            "session_key": routing_key,
            "started_at_monotonic": time.monotonic(),
            "task_text": full_task,
            "persist": persist,
        }
        if routing_key:
            self._session_tasks.setdefault(routing_key, set()).add(task_id)

        sk = routing_key

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_meta.pop(task_id, None)
            if sk and (ids := self._session_tasks.get(sk)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[sk]

        bg_task.add_done_callback(_cleanup)

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        *,
        task_id: str | None = None,
        persist: bool = False,
        resume: bool = False,
    ) -> str:
        """Spawn a subagent to execute a task in the background.

        When *persist* is True (e.g. ``/addagent``), the job is written to
        *workspace/.minibot/persistent_subagents.json* and survives process restarts
        until removed with ``/deleteagent`` or the run finishes and is no longer
        re-scheduled. Use *resume* when reloading from that store on startup.
        """
        if persist and not resume:
            tid = (task_id or str(uuid.uuid4())[:8])[:8]
        elif resume:
            if not task_id:
                raise ValueError("resume=True requires task_id")
            tid = str(task_id)[:8]
        else:
            tid = str(uuid.uuid4())[:8]
        display_label = (label or task[:30] + ("..." if len(task) > 30 else "")).strip() or "subagent"
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}
        routing_key = session_key if session_key else f"{origin_channel}:{origin_chat_id}"
        if persist and not resume:
            now_ts = time.time()
            self._persistence.upsert(
                {
                    "id": tid,
                    "label": display_label,
                    "task": task,
                    "session_key": routing_key,
                    "origin_channel": origin_channel,
                    "origin_chat_id": origin_chat_id,
                    "status": "running",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_run_started_at_unix": now_ts,
                }
            )
        elif persist and resume:
            recs = self._persistence.load_records()
            old = next((x for x in recs if str(x.get("id")) == tid), None)
            if old:
                old["status"] = "running"
                old["last_run_started_at_unix"] = time.time()
                old["task"] = task
                old["label"] = display_label
                self._persistence.upsert(old)
            else:
                # Legacy / missing record: create minimal row so it still lists
                self._persistence.upsert(
                    {
                        "id": tid,
                        "label": display_label,
                        "task": task,
                        "session_key": routing_key,
                        "origin_channel": origin_channel,
                        "origin_chat_id": origin_chat_id,
                        "status": "running",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "last_run_started_at_unix": time.time(),
                    }
                )

        self._schedule_subagent_task(
            tid, task, display_label, origin, routing_key, persist=persist
        )

        logger.info("Spawned subagent [{}]: {} (persist={})", tid, display_label, persist)
        return f"Subagent [{display_label}] started (id: {tid}). I'll notify you when it completes."

    def _persist_finish(
        self,
        task_id: str,
        final_status: str,
        *,
        duration_sec: int = 0,
        last_error: str | None = None,
    ) -> None:
        recs = self._persistence.load_records()
        old = next((x for x in recs if str(x.get("id")) == str(task_id)), None)
        if not old:
            return
        old["status"] = final_status
        old["last_run_ended_at_unix"] = time.time()
        old["last_run_duration_seconds"] = max(0, int(duration_sec))
        if last_error:
            old["last_error_excerpt"] = last_error[:500]
        elif "last_error_excerpt" in old and final_status != "error":
            old.pop("last_error_excerpt", None)
        self._persistence.upsert(old)

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        routing_session_key: str,
        *,
        persist: bool = False,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)
        wall_start = time.time()

        # Announce start so CLI shows the subagent is live
        await self._publish_subagent_progress(
            f"Starting task: {task.split(chr(10))[0][:120]}",
            origin=origin,
            task_id=task_id,
            label=label,
            tool_hint=False,
        )

        try:
            # Build subagent tools (no message tool, no spawn tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
            extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(DeleteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(GlobTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(GrepTool(workspace=self.workspace, allowed_dir=allowed_dir))
            if self.exec_config.enable:
                tools.register(ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                    allow_patterns=self.exec_config.allow_patterns,
                    deny_patterns=self.exec_config.deny_patterns,
                    allowed_commands=merge_subagent_exec_allowed_commands(
                        self.exec_config.allowed_commands
                    ),
                ))
            if self.web_config.enable:
                tools.register(WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy))
                tools.register(WebFetchTool(proxy=self.web_config.proxy))
            system_prompt = self._build_subagent_prompt()
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            result = await self.runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=15,
                max_tool_result_chars=self.max_tool_result_chars,
                hook=_SubagentBusHook(task_id, self, label, origin),
                max_iterations_message="Task completed but no final response was generated.",
                error_message=None,
                fail_on_tool_error=True,
            ))
            if result.stop_reason == "tool_error":
                if persist:
                    self._persist_finish(
                        task_id, "error",
                        duration_sec=int(time.time() - wall_start),
                        last_error=self._format_partial_progress(result)[:2000],
                    )
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    self._format_partial_progress(result),
                    origin,
                    "error",
                    routing_session_key,
                )
                return
            if result.stop_reason == "error":
                if persist:
                    self._persist_finish(
                        task_id, "error",
                        duration_sec=int(time.time() - wall_start),
                        last_error=result.error or "subagent execution failed",
                    )
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    result.error or "Error: subagent execution failed.",
                    origin,
                    "error",
                    routing_session_key,
                )
                return
            final_result = result.final_content or "Task completed but no final response was generated."
            if persist:
                self._persist_finish(
                    task_id, "completed",
                    duration_sec=int(time.time() - wall_start),
                )
            logger.info("Subagent [{}] completed successfully", task_id)
            await self._announce_result(
                task_id, label, task, final_result, origin, "ok", routing_session_key,
            )

        except asyncio.CancelledError:
            if persist:
                self._persist_finish(
                    task_id, "interrupted",
                    duration_sec=int(time.time() - wall_start),
                )
            raise
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            if persist:
                self._persist_finish(
                    task_id, "error",
                    duration_sec=int(time.time() - wall_start),
                    last_error=error_msg,
                )
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(
                task_id, label, task, error_msg, origin, "error", routing_session_key,
            )

    async def _publish_subagent_progress(
        self,
        content: str,
        *,
        origin: dict[str, str],
        task_id: str,
        label: str,
        tool_hint: bool = False,
    ) -> None:
        """Publish a subagent progress line to the outbound message bus for CLI display."""
        from minibot.bus.events import OutboundMessage
        await self.bus.publish_outbound(OutboundMessage(
            channel=origin["channel"],
            chat_id=origin["chat_id"],
            content=content,
            metadata={
                "_progress": True,
                "_tool_hint": tool_hint,
                "_agent_role": "subagent",
                "_subagent_id": task_id,
                "_subagent_label": label,
            },
        ))

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        routing_session_key: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        # Emit a visible tail line in CLI so the user sees the subagent closing
        tail = "Completed." if status == "ok" else f"Failed: {result[:100]}"
        await self._publish_subagent_progress(
            tail,
            origin=origin,
            task_id=task_id,
            label=label,
            tool_hint=False,
        )

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        # chat_id stays "channel:chat_id" for _process_message system-branch parsing;
        # session_key_override must match AgentLoop's effective key (incl. unified session).
        origin_chat = f"{origin['channel']}:{origin['chat_id']}"
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=origin_chat,
            content=announce_content,
            session_key_override=routing_session_key,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    def _build_subagent_prompt(self) -> str:
        """Build a focused system prompt for the subagent."""
        from minibot.agent.context import ContextBuilder
        from minibot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        skills_summary = SkillsLoader(
            self.workspace,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_ctx,
            workspace=str(self.workspace),
            skills_summary=skills_summary or "",
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )

    def list_running_by_session(self, session_key: str) -> list[dict[str, Any]]:
        """Return running (in-process) subagents for a session with id/label/elapsed seconds."""
        now = time.monotonic()
        rows: list[dict[str, Any]] = []
        tids = self._session_tasks.get(session_key, set())
        for tid in sorted(tids):
            task = self._running_tasks.get(tid)
            if task is None or task.done():
                continue
            meta = self._task_meta.get(tid, {})
            started_at = meta.get("started_at_monotonic", meta.get("started_at"))
            elapsed = 0
            if isinstance(started_at, (int, float)):
                elapsed = max(0, int(now - started_at))
            rows.append(
                {
                    "id": tid,
                    "label": str(meta.get("label") or ""),
                    "elapsed_seconds": elapsed,
                }
            )
        return rows

    def list_agents_for_session(self, session_key: str) -> list[dict[str, Any]]:
        """All persisted subagents for this session (incl. completed), with live uptime when running."""
        out: list[dict[str, Any]] = []
        for r in sorted(
            self._persistence.load_records(),
            key=lambda x: (str(x.get("label") or ""), str(x.get("id") or "")),
        ):
            if str(r.get("session_key") or "") != str(session_key):
                continue
            rid = str(r.get("id") or "")
            st = str(r.get("status") or "?")
            t = self._running_tasks.get(rid)
            running = t is not None and not t.done()
            if running and rid in self._task_meta:
                m = self._task_meta[rid]
                start_m = m.get("started_at_monotonic", m.get("started_at"))
                if isinstance(start_m, (int, float)):
                    elapsed = max(0, int(time.monotonic() - start_m))
                else:
                    elapsed = 0
            else:
                elapsed = int(r.get("last_run_duration_seconds") or 0)
            duty = str(r.get("task") or "")
            out.append(
                {
                    "id": rid,
                    "label": str(r.get("label") or ""),
                    "status": st,
                    "elapsed_seconds": elapsed,
                    "duty": duty,
                }
            )
        return out

    async def delete_by_label(self, session_key: str, label: str) -> tuple[int, int]:
        """Remove persisted subagents whose label matches (exact, trimmed). Returns (removed, cancelled)."""
        label = (label or "").strip()
        if not label:
            return 0, 0
        targets = [
            r
            for r in self._persistence.load_records()
            if str(r.get("session_key") or "") == str(session_key)
            and (str(r.get("label") or "")).strip() == label
        ]
        ids = {str(x.get("id")) for x in targets if x.get("id")}
        if not ids:
            return 0, 0
        cancelled = 0
        tasks: list[asyncio.Task[None]] = []
        for tid in ids:
            t = self._running_tasks.get(tid)
            if t is not None and not t.done():
                t.cancel()
                tasks.append(t)
                cancelled += 1
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        removed = self._persistence.remove_ids(ids)
        return removed, cancelled
