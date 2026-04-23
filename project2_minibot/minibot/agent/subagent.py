"""Subagent manager for background task execution."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

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
from minibot.agent.tools.shell import ExecTool
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

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}
        routing_key = session_key if session_key else f"{origin_channel}:{origin_chat_id}"

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin, routing_key)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        routing_session_key: str,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

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
                    allowed_commands=self.exec_config.allowed_commands,
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

            logger.info("Subagent [{}] completed successfully", task_id)
            await self._announce_result(
                task_id, label, task, final_result, origin, "ok", routing_session_key,
            )

        except Exception as e:
            error_msg = f"Error: {str(e)}"
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
