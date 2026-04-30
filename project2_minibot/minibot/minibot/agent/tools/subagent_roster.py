"""List and register standby subagents for the current chat session (discovery + /addagent-compatible writes)."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from minibot.agent.tools.base import Tool, tool_parameters
from minibot.agent.tools.schema import BooleanSchema, StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from minibot.agent.subagent import SubagentManager

_LABEL_MAX = 128
_DUTY_MIN = 8
_DUTY_MAX = 16000
_BUDGET_MAX = 3
_BUDGET_WINDOW_SEC = 300.0

_budget_lock = threading.Lock()
_register_windows: dict[str, tuple[int, float]] = {}


def _check_register_budget(session_key: str) -> str | None:
    """Return an error if this session cannot start another register/update (without consuming quota)."""
    now = time.monotonic()
    with _budget_lock:
        count, start = _register_windows.get(session_key, (0, now))
        if now - start > _BUDGET_WINDOW_SEC:
            count, start = 0, now
        if count >= _BUDGET_MAX:
            return (
                f"Error: at most {_BUDGET_MAX} roster register/update operations per "
                f"{int(_BUDGET_WINDOW_SEC)}s in this chat session; try again later."
            )
    return None


def _consume_register_budget(session_key: str) -> None:
    """Record one successful register/update for rate limiting."""
    now = time.monotonic()
    with _budget_lock:
        count, start = _register_windows.get(session_key, (0, now))
        if now - start > _BUDGET_WINDOW_SEC:
            count, start = 0, now
        _register_windows[session_key] = (count + 1, start)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "`list` — persisted subagents for this chat session only. "
            "`register` — save a standby duty under `label` (same persistence as `/addagent`).",
            enum=("list", "register"),
        ),
        label=StringSchema(
            "Short label for the standby subagent (required for `register`). "
            "Must be unique per session; conflicts require `update_existing`.",
        ),
        duty=StringSchema(
            "Standing duty / role text (required for `register`). "
            "Same field as `/addagent` duty; used when starting with `spawn` + `from_persisted_label`. "
            f"For `register`, length {_DUTY_MIN}–{_DUTY_MAX} chars.",
        ),
        acknowledge_create=BooleanSchema(
            description=(
                "Must be **true** when registering a **new** label (guards against accidental mass creation)."
            ),
            default=False,
        ),
        update_existing=BooleanSchema(
            description=(
                "Must be **true** to change the saved duty when `label` already exists in this session "
                "(prevents silent overwrites)."
            ),
            default=False,
        ),
        required=["action"],
    )
)
class SubagentRosterTool(Tool):
    """Session-scoped roster for persisted subagents (read + controlled register)."""

    def __init__(self, manager: SubagentManager):
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
        """Bind roster operations to the active routing session (never use model-supplied session ids)."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = routing_session_key or f"{channel}:{chat_id}"

    @property
    def name(self) -> str:
        return "subagent_roster"

    @property
    def description(self) -> str:
        return (
            "Inspect or register persisted subagents for the **current chat session** only. "
            "Uses the workspace store under `.minibot/persistent_subagents.json`; scope is always "
            "this session's routing key (you cannot pass a different session id). "
            "For `register`, new labels require `acknowledge_create=true`; existing labels require "
            "`update_existing=true` to replace duty. Does not start execution — use `spawn` with "
            "`from_persisted_label` or `/runagent` after registering. "
            "`completed` records are callable and can be re-run."
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        base = super().validate_params(params)
        if base:
            return base
        action = str(params.get("action") or "").strip()
        if action == "list":
            return []
        if action == "register":
            label = (params.get("label") or "").strip() if isinstance(params.get("label"), str) else ""
            duty = (params.get("duty") or "").strip() if isinstance(params.get("duty"), str) else ""
            if not label:
                return ["`register` requires non-empty `label`"]
            if not duty:
                return ["`register` requires non-empty `duty`"]
            if len(label) > _LABEL_MAX:
                return [f"`label` must be at most {_LABEL_MAX} chars"]
            if "\n" in label or "\r" in label:
                return ["`label` must not contain newlines"]
            if len(duty) < _DUTY_MIN:
                return [f"`duty` must be at least {_DUTY_MIN} chars"]
            if len(duty) > _DUTY_MAX:
                return [f"`duty` must be at most {_DUTY_MAX} chars"]
        return []

    def _format_list(self) -> str:
        sk = self._session_key
        rows = self._manager.list_agents_for_session(sk)
        path = self._manager.persistence_path
        lines = [
            f"Persisted subagents (session `{sk}`). Store: `{path}`",
            "",
            "| id | label | status | elapsed_s | duty (preview) |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in rows:
            duty = str(r.get("duty") or "")
            preview = duty[:120] + ("…" if len(duty) > 120 else "")
            preview = preview.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {r.get('id', '')} | {r.get('label', '')} | {r.get('status', '')} | "
                f"{r.get('elapsed_seconds', 0)} | {preview} |"
            )
        if not rows:
            lines.append("| — | — | — | — | *(no standby rows for this session)* |")
        return "\n".join(lines)

    async def execute(
        self,
        action: str,
        label: str | None = None,
        duty: str | None = None,
        acknowledge_create: bool = False,
        update_existing: bool = False,
        **kwargs: Any,
    ) -> str:
        act = (action or "").strip()
        if act == "list":
            return self._format_list()

        if act != "register":
            return "Error: `action` must be `list` or `register`."

        lab = (label or "").strip()
        dut = (duty or "").strip()
        ack = bool(acknowledge_create)
        upd = bool(update_existing)

        sk = self._session_key
        try:
            rec = self._manager._find_persisted_record(sk, label=lab)
        except ValueError as e:
            return f"Error: {e}"

        if rec is None:
            if not ack:
                return (
                    "Error: this would **create** a new standby subagent. "
                    "Set `acknowledge_create=true` after confirming the user wants it, "
                    "then retry with the same `label` and `duty`."
                )
        else:
            if not upd:
                return (
                    f"Error: label `{lab}` already exists in this session. "
                    "Set `update_existing=true` to replace the saved duty, or use `spawn` with "
                    "`from_persisted_label` to run without changing the stored duty."
                )

        if berr := _check_register_budget(sk):
            return berr

        out = self._manager.register_standby(
            dut,
            lab,
            self._origin_channel,
            self._origin_chat_id,
            sk,
        )
        if out.startswith("Error:"):
            return out
        _consume_register_budget(sk)
        return out
