"""Text-based ReAct loop (Thought / Action / Observation) without function-calling APIs."""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

from minibot.agent.tools.registry import ToolRegistry
from minibot.providers.base import LLMProvider
from minibot.utils.helpers import strip_think, truncate_text
from minibot.utils.react_display import format_react_text

# User-facing toggle phrases (exact match on stripped message).
# Include 打开/开启 — users often say「打开react模式」; without these, the LLM may assume React.js.
REACT_TOGGLE_ON_PHRASES: frozenset[str] = frozenset(
    {
        "开启react推理循环模式",
        "打开react推理循环模式",
        "开启react循环推理模式",
        "打开react循环推理模式",
        "开启ReAct推理循环模式",
        "打开ReAct推理循环模式",
        "开启ReAct循环推理模式",
        "打开ReAct循环推理模式",
        # legacy short phrases
        "开启react模式",
        "打开react模式",
    }
)
REACT_TOGGLE_OFF_PHRASES: frozenset[str] = frozenset(
    {
        "关闭react推理循环模式",
        "退出react推理循环模式",
        "关闭react循环推理模式",
        "退出react循环推理模式",
        "关闭ReAct推理循环模式",
        "退出ReAct推理循环模式",
        "关闭ReAct循环推理模式",
        "退出ReAct循环推理模式",
        "关闭react模式",
        "退出react模式",
    }
)
# Canonical strings (documentation / tests)
REACT_TOGGLE_ON = "开启react推理循环模式"
REACT_TOGGLE_OFF = "关闭react推理循环模式"

# Normalization for fuzzy toggle intent detection: strip polite prefixes and
# trailing punctuation so "请退出推理循环模式" matches the same intent as
# "退出推理循环模式". Common typos (e.g. 旅理 -> 推理) are also folded.
_TOGGLE_PREFIX_RE = re.compile(r"^(请你?|麻烦|帮我|请帮我|能否|可否|请问)\s*")
_TOGGLE_SUFFIX_RE = re.compile(r"[，,。！？!?\s]+$")
_TOGGLE_TYPO_MAP = (
    ("旅理", "推理"),
    ("retract", "react"),
)


def _normalize_toggle_input(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = _TOGGLE_SUFFIX_RE.sub("", s)
    s = _TOGGLE_PREFIX_RE.sub("", s)
    s_low = s.lower().replace(" ", "")
    for wrong, right in _TOGGLE_TYPO_MAP:
        s_low = s_low.replace(wrong, right)
    return s_low


def detect_react_toggle(raw: str) -> str | None:
    """Return ``"on"``/``"off"`` when the message is a ReAct toggle intent.

    Goals:
    * Preserve existing exact-phrase behavior (legacy tests / docs).
    * Accept common natural-language variations: polite prefixes (``请``/``麻烦``…),
      missing ``react`` keyword (``"退出推理循环模式"``), the frequent typo
      ``旅理`` → ``推理``, and English variants (``"disable react"``).
    * Avoid false positives in long / complex messages: only trigger when the
      normalized message is short and clearly a toggle command.
    """
    if not raw:
        return None
    norm = _normalize_toggle_input(raw)
    if not norm:
        return None

    # 1) Exact match against the canonical phrase sets (case/space-insensitive).
    for phrase in REACT_TOGGLE_ON_PHRASES:
        if norm == phrase.lower().replace(" ", ""):
            return "on"
    for phrase in REACT_TOGGLE_OFF_PHRASES:
        if norm == phrase.lower().replace(" ", ""):
            return "off"

    # 2) Fuzzy fallback. Guard against long messages to avoid accidental matches.
    if len(norm) > 30:
        return None

    has_off_verb = any(v in norm for v in ("关闭", "退出", "结束", "停止", "disable", "turnoff", "exit"))
    has_on_verb = any(v in norm for v in ("开启", "打开", "启用", "启动", "enable", "turnon"))

    mentions_mode = (
        "react" in norm
        or ("推理" in norm and ("循环" in norm or "模式" in norm))
    )
    if not mentions_mode:
        return None

    if has_off_verb and not has_on_verb:
        return "off"
    if has_on_verb and not has_off_verb:
        return "on"
    return None

# Tools that must not run during text ReAct (they can emit to the user before Action: finish).
REACT_TEXT_EXCLUDE_TOOLS: frozenset[str] = frozenset({"message"})

# -----------------------------------------------------------------------------
# State-change intent guardrail
# -----------------------------------------------------------------------------
# Some models (observed on qvq-max-2025-03-25 and other reasoning models) will
# gladly emit ``Action: finish`` with a *fabricated* Observation like
# "文件已创建" for a write/delete/edit request — without ever calling the
# matching tool. The prompt already forbids this (see build_react_appendix
# category B), but prompt alone is not sufficient for weaker instruction
# following. When the last user message clearly asks for a file state change
# and no state-changing tool ran during the loop, we reject the finish and
# send a forceful correction Observation, giving the model another chance to
# actually invoke ``write_file`` / ``edit_file`` / ``notebook_edit`` / ``exec``.
#
# Tools that satisfy the guard: anything that can actually create/modify/
# delete files on disk in this workspace.
REACT_STATE_CHANGE_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "delete_file", "notebook_edit", "exec"}
)

# How many times we reject a hallucinated finish before giving up and letting
# the finish through. 2 is enough in practice (model almost always complies on
# the first retry); higher values mostly waste tokens on a pathological model.
MAX_STATE_CHANGE_REJECTIONS: int = 2

_STATE_CHANGE_VERBS_ZH: tuple[str, ...] = (
    "新建", "创建", "建立", "写入", "写到", "写进",
    "保存到", "保存为", "追加到", "追加进",
    "删除", "移除", "清除", "清空",
    "修改", "编辑", "更新", "更改", "替换", "改写", "重命名",
)
_STATE_CHANGE_VERBS_EN: tuple[str, ...] = (
    "create", "make", "write", "save", "append", "overwrite",
    "delete", "remove", "erase",
    "modify", "edit", "update", "rename", "replace",
)
_FILE_CONTEXT_TOKENS_ZH: tuple[str, ...] = (
    "文件", "文档", "目录", "文件夹", "workspace", "工作区", "工作目录",
)
_FILE_CONTEXT_TOKENS_EN: tuple[str, ...] = (
    "file", "directory", "folder", "workspace",
)
_FILE_EXT_RE = re.compile(
    r"\.(txt|md|json|ya?ml|py|pyi|js|mjs|cjs|ts|tsx|jsx|java|c|cc|cpp|cxx|hpp|h|hh|rs|go|rb|php|sh|bash|ps1|html?|css|scss|less|toml|ini|cfg|conf|xml|csv|tsv|log|ipynb|sql|rst|env|lock|bat|cmd)\b",
    re.IGNORECASE,
)


def detect_file_state_change_intent(text: str) -> bool:
    """Heuristic: does *text* ask the agent to create/modify/delete a file?

    Requires BOTH a state-change verb AND a file/workspace context anchor so
    benign messages like "新建一个函数" (code suggestion) or "修改一下你的语气"
    (no files involved) don't false-positive.
    """
    if not text:
        return False
    s = text.lower()
    has_verb = any(v in text for v in _STATE_CHANGE_VERBS_ZH) or any(
        v in s for v in _STATE_CHANGE_VERBS_EN
    )
    if not has_verb:
        return False
    has_context = (
        any(t in text for t in _FILE_CONTEXT_TOKENS_ZH)
        or any(t in s for t in _FILE_CONTEXT_TOKENS_EN)
        or bool(_FILE_EXT_RE.search(text))
    )
    return has_context


_DELETE_VERBS_ZH: tuple[str, ...] = ("删除", "移除", "清除", "清空")
_DELETE_VERBS_EN: tuple[str, ...] = ("delete", "remove", "erase", "unlink")
# Verbs that imply a create/write intent which would veto "delete-only".
_WRITE_VERBS_ZH: tuple[str, ...] = (
    "新建", "创建", "建立", "写入", "写到", "写进",
    "保存到", "保存为", "追加到", "追加进",
    "修改", "编辑", "更新", "更改", "替换", "改写", "重命名",
)
_WRITE_VERBS_EN: tuple[str, ...] = (
    "create", "make", "write", "save", "append", "overwrite",
    "modify", "edit", "update", "rename", "replace",
)


def _is_delete_only_intent(text: str) -> bool:
    """True when *text* asks only for deletion (not create + delete in one turn).

    Used by the ReAct state-change guardrail to suggest ``delete_file`` as the
    primary retry tool for deletion requests, rather than defaulting to
    ``write_file`` (which would then force the model into ``exec`` for delete
    — exactly the Unicode/cmd.exe trap we're trying to avoid on Windows).
    """
    if not text:
        return False
    low = text.lower()
    has_delete_verb = any(v in text for v in _DELETE_VERBS_ZH) or any(
        v in low for v in _DELETE_VERBS_EN
    )
    if not has_delete_verb:
        return False
    has_write_verb = any(v in text for v in _WRITE_VERBS_ZH) or any(
        v in low for v in _WRITE_VERBS_EN
    )
    return not has_write_verb


def _extract_last_user_text(messages: list[dict[str, Any]]) -> str:
    """Return the plain-text payload of the most recent user turn, for intent checks."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts: list[str] = []
            for blk in c:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(str(blk.get("text", "")))
            return "\n".join(parts)
        return str(c or "")
    return ""


_CN_NUM = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")


def cycle_title(n: int) -> str:
    """第 N 轮循环 — 与用户示例「第一轮循环」一致（1–10 用中文数字）。"""
    if 1 <= n <= 10:
        digit = _CN_NUM[n] if n < 10 else "十"
        return f"第{digit}轮循环"
    return f"第{n}轮循环"


def format_tool_catalog(definitions: list[dict[str, Any]]) -> str:
    """Serialize OpenAI-style tool definitions as readable JSON for the prompt."""
    lines: list[str] = []
    for d in definitions:
        try:
            lines.append(json.dumps(d, ensure_ascii=False, indent=2))
        except TypeError:
            lines.append(str(d))
    return "\n\n".join(lines)


def _append_react_protocol(messages: list[dict[str, Any]], appendix: str) -> None:
    """Append ReAct instructions to the first system message, or insert a new system message."""
    tag = "\n\n---\n\n# ReAct mode protocol\n\n"
    for i, m in enumerate(messages):
        if m.get("role") != "system":
            continue
        c = m.get("content")
        if isinstance(c, str):
            messages[i] = {**m, "content": c + tag + appendix}
        elif isinstance(c, list):
            merged = list(c)
            merged.append({"type": "text", "text": tag + appendix})
            messages[i] = {**m, "content": merged}
        else:
            messages[i] = {**m, "content": tag.strip() + appendix}
        return
    messages.insert(0, {"role": "system", "content": "# ReAct mode protocol\n\n" + appendix})


def build_react_appendix(tools: ToolRegistry) -> str:
    catalog = format_tool_catalog(tools.get_definitions())
    return (
        "**ReAct** = **Reasoning + Acting** (Thought / Action / Observation text protocol). "
        "It is NOT the React.js / npm / frontend stack.\n\n"
        "## Turn scope / 本轮范围 (mandatory)\n"
        "The **last user message** in the thread is the **only** task you must satisfy in this run. "
        "Older messages are background; do **not** merge unrelated past questions into the current answer.\n"
        "在**本轮**的 `Action: finish` 里，只回答**当前最后一条用户消息**里的问题。"
        "不要复述、不要再次输出上一轮已经给过的算式、数字、结论或全文，除非用户**明确要求**重复。\n"
        "若当前问题与更早的话题无关，请**完全忽略**旧话题，Thought 里也不要再分析旧问题。\n"
        "Your `Thought` must focus on the **latest** user text only; do not write \"用户问了多个问题\" "
        "unless the latest message literally contains multiple distinct questions.\n\n"
        "**Session rule (mandatory):** The user has **already enabled ReAct reasoning** in this chat. "
        "You MUST NOT ask them to confirm \"React 应用 / 开发服务器\" vs \"推理 ReAct\". "
        "Do NOT mention npm, package.json, create-react-app, Vite, webpack, or dev servers unless "
        "the user explicitly asks for a frontend task. Do NOT read package.json to \"check React project\".\n\n"
        "**Output rule:** The user-visible reply must come **only** from `Action: finish`. "
        "Use tools only for computation / file / search steps; do not try to \"send a chat message\" "
        "except via `finish` (the `message` tool is unavailable in this mode).\n\n"
        "For arithmetic or short factual questions: use at most one or two tool rounds if needed, "
        "then `Action: finish` with the concise answer in `Observation`. No lecturing about React.js.\n\n"
        "## When to call a tool vs. when to finish directly (mandatory)\n"
        "Split every request into one of two categories BEFORE you act:\n\n"
        "**(A) Knowledge-only questions** — jokes, greetings, translation, summarization of the user's own text, "
        "定义/解释类问题 where you already have the factual answer from your own knowledge: "
        "do NOT call any tool. Respond in one step with `Action: finish`.\n"
        "Never invent tool names (e.g. `tell_joke`, `generate_joke`), and never pipe an answer "
        "through `web_search` / `web_fetch` when you can answer from your own knowledge.\n\n"
        "**(B) Action / side-effectful / state-changing tasks** — create/modify/delete files, run code, "
        "edit notebooks, browse the web for fresh information, spawn subagents, schedule crons, etc.: "
        "you MUST actually call the matching tool (`write_file`, `edit_file`, `delete_file`, `exec`, `web_search`, …). "
        "You have **full access** to the tools listed in the catalog below and they operate on the real "
        "workspace — do **not** claim you \"lack permission\" or \"cannot create files\", and do **not** "
        "emit a shell one-liner as the answer instead of calling the tool.\n"
        "If the user says \"新建 xxx 文件 / 写入 / 修改 / 删除 / 运行\" or any imperative that changes state, "
        "category (B) applies and `Action: finish` alone is not acceptable — you must invoke a tool first, "
        "observe its result, and only then `Action: finish` to report success.\n\n"
        "**Anti-hallucination rule (hard ban):** For file-system writes/edits/deletes you MUST NOT go "
        "`Action: finish` on round 1 with an Observation like "
        "『文件 \"xxx.txt\" 已创建 / 内容为 ... / 文件已创建于 workspace 目录』 "
        "unless a prior round actually returned that result from `write_file` / `edit_file` / "
        "`delete_file` / `notebook_edit` / `exec`. Fabricating a success Observation without having called the tool is "
        "**lying** — the environment will detect it, reject your finish, and force you to call the tool "
        "for real. The only way to satisfy 『创建/写入/删除/修改 文件』 is to run the matching tool and "
        "then report its *real* return value.\n\n"
        "Minimal example for a knowledge-only request (category A)::\n\n"
        "    Thought: 讲一个程序员笑话，直接回答即可，无需工具。\n"
        "    Action: finish\n"
        "    Observation: \"为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。\"\n\n"
        "Minimal example for a write-file task (category B)::\n\n"
        "    Thought: 用户要求在 workspace 新建 测试2.txt 并写入内容；这是状态改变任务，必须用 write_file。\n"
        "    Action: write_file\n"
        "    Observation: {\"path\": \"测试2.txt\", \"content\": \"第二次写入测试\"}\n"
        "    (上一行是工具参数 JSON；工具真实执行后返回的 Observation 会替换此处。)\n"
        "    # 收到真实 Observation 后的下一轮：\n"
        "    Thought: 写入已成功，可以向用户报告。\n"
        "    Action: finish\n"
        "    Observation: \"已在 workspace 新建 测试2.txt 并写入『第二次写入测试』。\"\n\n"
        "You are in **ReAct mode**. Do NOT rely on function-calling / tool APIs. "
        "Each turn, output **only** plain text in this exact shape:\n\n"
        "Thought: <your reasoning>\n"
        "Action: <tool_name_only> OR Action: finish\n"
        "  (Write **only** the function name in Action — no parentheses or JSON on this line.)\n"
        "Observation: <first line: one JSON value — tool parameters object, or finish answer string/object>\n"
        "<optional following lines: 事实对齐 — briefly align expected tool outcome or finish content with your Thought, "
        "before the environment returns the real tool observation.>\n\n"
        "Rules:\n"
        "- For a tool call, `Action` is **only** the tool name from the catalog; put parameters as JSON on the "
        "**first line** after `Observation:`.\n"
        "- After that first JSON line, you may add free text lines that state how you align reasoning with the "
        "planned action / expected observation (事实对齐).\n"
        "- To finish, use `Action: finish` (or the synonyms `final_answer` / `final` / `answer` / `done`) "
        "and put the user-visible answer on the **first line** of `Observation` as a JSON string or as "
        "{\"answer\": \"...\"}. The loop terminates as soon as a valid finish step is parsed.\n"
        "- Legacy `Action Input:` is still accepted if you forget `Observation:`.\n"
        "- Do not wrap the whole reply in ```` ```json ```` (or any other) code fence.\n"
        "- Do not emit a single JSON object like `{\"Thought\": ..., \"Action\": ..., \"Observation\": ...}`. "
        "Use real line-prefixed labels (three separate lines).\n"
        "- Prefer plain lines starting with `Thought:` / `Action:` / `Observation:` (column 0 after spaces). "
        "If you wrap labels in `**bold**` (e.g. `**Action:**`) or inside a code fence, the runtime will "
        "normalize them, but plain labels are more reliable.\n\n"
        "## Available tools (JSON schemas)\n\n"
        f"{catalog}\n"
    )


def inject_react_instructions(
    messages: list[dict[str, Any]],
    tools: ToolRegistry,
) -> list[dict[str, Any]]:
    out = [dict(m) for m in messages]
    _append_react_protocol(out, build_react_appendix(tools))
    return out


_REACT_LINE_HEADER = re.compile(
    r"^(\s*)\*{0,2}\s*(Thought|Action|Observation)\s*:\s*\*{0,2}\s*(.*)$",
    re.IGNORECASE,
)

# Matches a full-text enclosing Markdown code fence with optional language tag:
# e.g. ``` ... ```  or  ```json ... ```  (DOTALL so body can span lines).
_CODE_FENCE_RE = re.compile(
    r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$",
    re.DOTALL,
)


def _strip_enclosing_code_fence(text: str) -> str:
    """Remove a single enclosing ``` code fence (possibly with language tag)."""
    m = _CODE_FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text


def _try_unfold_react_json_object(text: str) -> str | None:
    """Unfold a JSON object using ReAct keys into canonical protocol lines.

    Accepts::

        {"Thought": "...", "Action": "finish", "Observation": {"answer": "..."}}

    and returns::

        Thought: ...
        Action: finish
        Observation: {"answer": "..."}

    Returns ``None`` when the text is not a JSON object with at least
    ``Action`` + ``Observation`` keys (case-insensitive).
    """
    stripped = text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    keys_lower = {k.lower(): k for k in obj.keys() if isinstance(k, str)}
    if "action" not in keys_lower or "observation" not in keys_lower:
        return None

    thought = obj.get(keys_lower.get("thought", ""), "")
    action = obj.get(keys_lower["action"])
    observation = obj.get(keys_lower["observation"])

    def _to_line_value(val: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, str):
            return val
        return json.dumps(val, ensure_ascii=False)

    def _to_observation_body(val: Any) -> str:
        # Observation's first line needs to be JSON-parseable for tool calls
        # (dict/list/primitive). For finish, a plain string is fine — serialize
        # it as a JSON string so the existing parser accepts it.
        if isinstance(val, (dict, list, bool, int, float)) or val is None:
            return json.dumps(val, ensure_ascii=False)
        return json.dumps(val, ensure_ascii=False)

    parts: list[str] = []
    if isinstance(thought, str) and thought.strip():
        parts.append(f"Thought: {thought.strip()}")
    elif thought:
        parts.append(f"Thought: {_to_line_value(thought)}")
    parts.append(f"Action: {_to_line_value(action).strip()}")
    parts.append(f"Observation: {_to_observation_body(observation)}")
    return "\n".join(parts)


def normalize_react_markdown_headers(text: str) -> str:
    """Strip Markdown bold wrappers around ReAct labels (e.g. ``**Thought:**``).

    Models often emit ``**Thought:**`` / ``**Action:**`` / ``**Observation:**``;
    :func:`parse_react_step` expects plain ``Thought:`` lines at column 0 (after
    whitespace), so we normalize before regex parsing.
    """
    if not text:
        return text
    out: list[str] = []
    for line in text.split("\n"):
        m = _REACT_LINE_HEADER.match(line)
        if m:
            indent, lab, rest = m.group(1), m.group(2), m.group(3)
            canon = {
                "thought": "Thought",
                "action": "Action",
                "observation": "Observation",
            }[lab.lower()]
            out.append(f"{indent}{canon}: {rest}")
        else:
            out.append(line)
    return "\n".join(out)


def preprocess_react_model_output(text: str) -> str:
    """Best-effort normalization of common LLM formatting deviations.

    Handles (in order):

    1. ``<think>``/``<thought>`` reasoning blocks (via :func:`strip_think`).
    2. Whole-output Markdown code fences (```` ```json ... ``` ````).
    3. Whole-output JSON objects keyed by ``Thought`` / ``Action`` /
       ``Observation`` — unfolded back to canonical protocol lines.
    4. Per-line Markdown bold around labels (``**Thought:**`` …).
    """
    raw = strip_think((text or "").strip())
    if not raw:
        return raw
    raw = _strip_enclosing_code_fence(raw)
    unfolded = _try_unfold_react_json_object(raw)
    if unfolded is not None:
        raw = unfolded
    raw = normalize_react_markdown_headers(raw)
    return raw


_REACT_LABEL_ANY_RE = re.compile(
    r"^\s*(Thought|Action|Observation)\s*:", re.MULTILINE | re.IGNORECASE
)

# Sentinel used in ``parse_react_step`` error strings to signal that the model
# emitted natural-language text with no ReAct labels at all. ``run_react_loop``
# recognizes it to send a much more explicit correction Observation, and
# optionally to accept the raw text as an implicit ``finish`` after repeated
# failures to avoid infinite retries.
NAKED_PLAIN_TEXT_ERROR = "naked plain text: no Thought:/Action:/Observation: labels"

# Safety caps for :func:`run_react_loop`. These protect against two classic
# failure modes that ``max_iterations`` alone does not catch cheaply:
#
# * The model keeps producing malformed ReAct steps that ``parse_react_step``
#   rejects round after round (protocol drift / JSON syntax errors).
# * The model keeps calling the same tool with inputs that keep erroring
#   (bad parameters / nonexistent tool name / provider failure).
#
# When either streak exceeds its cap, the loop terminates early with a
# distinct ``stop_reason`` instead of burning the entire ``max_iterations``
# budget on a stuck trajectory.
MAX_CONSECUTIVE_PARSE_ERRORS: int = 3
MAX_CONSECUTIVE_TOOL_ERRORS: int = 3

# Aliases for the terminal "give the final answer" action. We normalize them
# all to the canonical "finish" in parse_react_step. The original ReAct paper
# uses "Final Answer:" as the terminator, and many LLMs naturally emit
# ``Action: final_answer`` (or similar) — accepting these makes the loop
# tolerant of minor phrasing drift while keeping a single internal convention.
FINISH_ACTION_ALIASES: frozenset[str] = frozenset(
    {
        "finish",
        "final",
        "final_answer",
        "finalanswer",
        "answer",
        "done",
        "end",
        "stop",
        "terminate",
        "respond",
        "reply",
    }
)


def _is_finish_action(action_name: str) -> bool:
    """True when *action_name* is any recognized synonym for ``finish``.

    Tolerates common separators (space / hyphen / underscore) and case.
    """
    if not action_name:
        return False
    key = action_name.strip().lower().replace("-", "_").replace(" ", "_")
    return key in FINISH_ACTION_ALIASES


def _is_naked_plain_answer(text: str) -> bool:
    """True when the model emitted a non-empty reply with *no* ReAct labels."""
    if not text.strip():
        return False
    return _REACT_LABEL_ANY_RE.search(text) is None


def parse_react_step(
    text: str,
) -> tuple[str | None, str | None, Any | None, str | None, str | None]:
    """Parse one ReAct step.

    Returns (thought, action, payload, alignment_text, error).
    *payload* is JSON for tool params or finish answer; *alignment_text* is optional 事实对齐 after line 1.

    When the model reply contains **no** ReAct labels at all, returns the
    sentinel :data:`NAKED_PLAIN_TEXT_ERROR` as the error so the caller can
    surface a targeted correction message (preserving the 3-section protocol
    in the UI) instead of silently finishing with the raw text.
    """
    raw = preprocess_react_model_output(text)
    if not raw:
        return None, None, None, None, "empty model output"

    if _is_naked_plain_answer(raw):
        return None, None, None, None, NAKED_PLAIN_TEXT_ERROR

    thought: str | None = None
    tm = re.search(r"^Thought:\s*(.+?)(?=^Action:\s*)", raw, flags=re.DOTALL | re.MULTILINE)
    if tm:
        thought = tm.group(1).strip()

    am = re.search(r"^Action:\s*(.+)$", raw, flags=re.MULTILINE)
    if not am:
        return thought, None, None, None, "missing Action: line"

    action = am.group(1).strip()
    action_name = action.split()[0] if action else ""

    blob = _extract_observation_blob(raw)
    if blob is None:
        return thought, action_name or None, None, None, "missing Observation: line (or legacy Action Input:)"

    payload, alignment, perr = _parse_observation_payload(blob)
    if perr:
        return thought, action_name or None, None, None, perr

    if _is_finish_action(action_name):
        return thought, "finish", payload, alignment, None
    return thought, action_name, payload, alignment, None


def _extract_observation_blob(raw: str) -> str | None:
    """Prefer `Observation:`; fall back to legacy `Action Input:` for compatibility."""
    for pattern in (r"^Observation:\s*", r"^Action Input:\s*"):
        m = re.search(pattern, raw, flags=re.MULTILINE)
        if m:
            return raw[m.end() :].strip()
    return None


def _parse_observation_payload(blob: str) -> tuple[Any | None, str | None, str | None]:
    """Parse Observation body: first line (or whole blob) JSON + optional 事实对齐 in following lines.

    Lenient fallback: if neither the whole blob nor the first line parses as JSON,
    return the whole blob as a plain string. This lets ``Action: finish`` accept
    natural-language answers (``Observation: 这是答案``) without forcing the model
    to quote them. Tool calls that require a JSON object still fail downstream in
    :func:`normalize_tool_params`.
    """
    blob_stripped = blob.strip()
    if not blob_stripped:
        return None, None, "empty Observation"

    try:
        return json.loads(blob_stripped), None, None
    except json.JSONDecodeError:
        pass

    parts = blob_stripped.split("\n", 1)
    first = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    try:
        val = json.loads(first)
        alignment = rest if rest else None
        return val, alignment, None
    except json.JSONDecodeError:
        pass

    return blob_stripped, None, None


def finalize_finish_payload(raw: Any) -> str:
    """Turn finish payload from Observation into user-visible text."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict) and "answer" in raw:
        return str(raw["answer"])
    return json.dumps(raw, ensure_ascii=False)


def normalize_tool_params(payload: Any) -> dict[str, Any] | str:
    """Tool calls expect a JSON object; return str error marker if wrong."""
    if isinstance(payload, dict):
        return payload
    if payload is None:
        return {}
    return f"Error: Observation first line for tools must be a JSON object, got {type(payload).__name__}"


@dataclass
class ReactRunResult:
    """*final_content* is the user-visible reply: on successful ``finish``, only the answer text."""

    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    stop_reason: str = "completed"
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    # Full ReAct trace (rounds + 最终答案) for logging; not shown as the main chat reply.
    react_trace: str | None = None


async def _emit_progress(
    progress_callback: Callable[..., Any] | None,
    text: str,
) -> None:
    if progress_callback is None:
        return
    ret = progress_callback(text, tool_hint=False)
    if inspect.isawaitable(ret):
        await ret


async def run_react_loop(
    *,
    provider: LLMProvider,
    tools: ToolRegistry,
    initial_messages: list[dict[str, Any]],
    model: str,
    max_iterations: int,
    max_tool_result_chars: int,
    provider_retry_mode: str,
    progress_callback: Callable[..., Any] | None = None,
    on_stream_delta: Callable[..., Any] | None = None,
    on_stream_end: Callable[..., Any] | None = None,
    exclude_tools: frozenset[str] | None = None,
    emit_round_progress: bool = True,
) -> ReactRunResult:
    """Run a text-only ReAct loop (Thought / Action / Observation protocol).

    Design:

    * **ReAct pattern** — each LLM turn produces a ``Thought:`` / ``Action:`` /
      ``Observation:`` triple; :func:`parse_react_step` tolerates code fences,
      JSON-object wrappers, Markdown bold, and Chinese/English label variants.
    * **Multi-round tool use** — when ``Action`` names a tool, the tool's real
      result replaces the model's speculative Observation and is fed back as a
      fresh ``user`` message, then the model produces the next ReAct step.
      A complex task can therefore chain as many tool rounds as needed.
    * **Termination conditions** — the loop stops on the *first* of:

      1. ``Action: finish`` (or any of :data:`FINISH_ACTION_ALIASES` such as
         ``final_answer``) — :attr:`ReactRunResult.stop_reason` = ``completed``.
      2. ``max_iterations`` rounds without a finish — ``max_iterations``.
      3. Provider-level model error — ``error``.
      4. Safety caps to prevent runaway loops (not strictly required by the
         spec but essential for robustness):

         * two consecutive naked-plain-text rounds (model ignores the protocol)
           → synthesize a finish from the last reply → ``completed``;
         * :data:`MAX_CONSECUTIVE_PARSE_ERRORS` consecutive parse failures
           → ``parse_error_limit``;
         * :data:`MAX_CONSECUTIVE_TOOL_ERRORS` consecutive tool failures on the
           same tool → ``tool_error_limit``.

    Streaming plumbing:

    By default *emit_round_progress* is True so each round is sent via
    *progress_callback*. When *on_stream_delta* is set, each LLM completion
    streams via ``chat_stream_with_retry`` (token deltas). The per-round title
    (``第N轮循环``) is prefixed to the **first non-empty** stream chunk so CLI
    spinners can stay active until the model begins streaming. *on_stream_end*
    finalizes one streamed segment (e.g. CLI ``StreamRenderer``):
    ``resuming=True`` between tool rounds so the next round does not append to
    the same buffer; ``resuming=False`` on the final finish output or on error.
    The user-facing answer comes only from the finish step — *final_content*
    is answer-only on success. Set *emit_round_progress* to False to suppress
    per-round progress.
    """
    exclude = exclude_tools if exclude_tools is not None else REACT_TEXT_EXCLUDE_TOOLS
    react_tools = tools.copy_excluding(exclude)
    messages = inject_react_instructions([dict(m) for m in initial_messages], react_tools)
    tools_used: list[str] = []
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    transcript_parts: list[str] = []
    # Safety counters — see the termination conditions in this function's docstring.
    naked_plain_text_streak = 0
    last_naked_plain_text = ""
    consecutive_parse_errors = 0
    consecutive_tool_errors = 0
    last_failing_tool: str | None = None

    # State-change intent guardrail — see REACT_STATE_CHANGE_TOOLS docs.
    # We compute intent once from the last user message in the thread (which
    # is the "current task" per the ReAct appendix) and then reject a bogus
    # Action: finish up to MAX_STATE_CHANGE_REJECTIONS times, forcing the model
    # to actually call write_file / edit_file / notebook_edit / exec.
    _last_user_text = _extract_last_user_text(initial_messages)
    state_change_intent = detect_file_state_change_intent(_last_user_text)
    # Only enforce when at least one state-change tool is actually available.
    state_change_enforceable = state_change_intent and any(
        react_tools.has(name) for name in REACT_STATE_CHANGE_TOOLS
    )
    state_change_rejections = 0

    def _acc_usage(u: dict[str, int] | None) -> None:
        if not u:
            return
        for k, v in u.items():
            try:
                usage[k] = usage.get(k, 0) + int(v)
            except (TypeError, ValueError):
                continue

    use_llm_stream = on_stream_delta is not None

    async def _maybe_stream_end(*, resuming: bool) -> None:
        if not use_llm_stream or on_stream_end is None:
            return
        r = on_stream_end(resuming=resuming)
        if inspect.isawaitable(r):
            await r

    for round_idx in range(1, max_iterations + 1):
        # Do **not** emit the round title as a separate on_stream_delta before the
        # LLM call: that would wake StreamRenderer immediately and stop the Rich
        # "thinking" spinner (minibot.cli.stream.ThinkingSpinner) while the model
        # is still waiting. Prefix the title onto the first non-empty streamed
        # chunk instead so the CLI green spinner covers TTFT for each round.
        _round_n = round_idx
        stream_round_title_sent = False

        async def _on_content_delta(delta: str) -> None:
            nonlocal stream_round_title_sent
            if on_stream_delta is None:
                return
            out = delta
            if use_llm_stream and not stream_round_title_sent and delta:
                out = f"{cycle_title(_round_n)}\n" + delta
                stream_round_title_sent = True
            r = on_stream_delta(out)
            if inspect.isawaitable(r):
                await r

        response = await provider.chat_stream_with_retry(
            messages=messages,
            tools=None,
            model=model,
            retry_mode=provider_retry_mode,
            on_content_delta=_on_content_delta if use_llm_stream else None,
        )
        _acc_usage(response.usage)
        if response.finish_reason == "error":
            err = response.content or "model error"
            logger.error("ReAct: model error: {}", err[:200])
            await _maybe_stream_end(resuming=False)
            tail = "\n\n".join(transcript_parts) if transcript_parts else ""
            trace = (tail + "\n\n" + err).strip() if tail else err
            return ReactRunResult(
                final_content=trace,
                messages=messages,
                tools_used=tools_used,
                stop_reason="error",
                usage=usage,
                error=err,
                react_trace=trace,
            )

        raw_text = strip_think(response.content or "") or ""
        if response.has_tool_calls:
            logger.warning("ReAct: model returned tool_calls despite tools=None; using text only")

        _, action, payload, alignment_text, perr = parse_react_step(raw_text)
        header = f"{cycle_title(round_idx)}\n{raw_text.strip()}\n"
        if use_llm_stream and on_stream_end is not None:
            if perr or action != "finish":
                await _maybe_stream_end(resuming=True)
            else:
                await _maybe_stream_end(resuming=False)

        if emit_round_progress:
            if not use_llm_stream:
                await _emit_progress(progress_callback, format_react_text(header))
            if alignment_text:
                await _emit_progress(
                    progress_callback,
                    f"事实对齐: {alignment_text}\n",
                )
        transcript_parts.append(header)
        if alignment_text:
            transcript_parts.append(f"事实对齐: {alignment_text}\n")

        messages.append({"role": "assistant", "content": raw_text})

        if perr:
            consecutive_parse_errors += 1
            if consecutive_parse_errors >= MAX_CONSECUTIVE_PARSE_ERRORS:
                # The model has been unable to produce a parseable ReAct step
                # for MAX_CONSECUTIVE_PARSE_ERRORS rounds in a row. Terminate
                # with a dedicated stop_reason instead of burning the remaining
                # iteration budget on the same failure mode.
                await _maybe_stream_end(resuming=False)
                reason_msg = (
                    f"连续 {consecutive_parse_errors} 轮无法解析 ReAct 步骤（最近一次：{perr}）。"
                    "已提前终止循环。"
                )
                if emit_round_progress:
                    await _emit_progress(progress_callback, reason_msg + "\n")
                transcript_parts.append(reason_msg + "\n")
                tail = "\n\n".join(transcript_parts).rstrip()
                logger.warning("ReAct: {}", reason_msg)
                return ReactRunResult(
                    final_content=reason_msg,
                    messages=messages,
                    tools_used=tools_used,
                    stop_reason="parse_error_limit",
                    usage=usage,
                    error=perr,
                    react_trace=tail,
                )
            if perr == NAKED_PLAIN_TEXT_ERROR:
                naked_plain_text_streak += 1
                last_naked_plain_text = raw_text.strip()
                # Second consecutive refusal to follow the protocol: accept the
                # plain-text answer as an implicit finish so the user isn't
                # stuck in an endless retry loop. The single-round case still
                # retries so the three-section protocol remains visible when
                # the model is capable of following instructions.
                if naked_plain_text_streak >= 2 and last_naked_plain_text:
                    synthesized_thought = (
                        "模型未按 ReAct 三段协议输出；将上一条自然语言回复直接作为最终答案。"
                    )
                    answer = last_naked_plain_text
                    obs_json = json.dumps(answer, ensure_ascii=False)
                    synthesized = (
                        f"Thought: {synthesized_thought}\n"
                        f"Action: finish\n"
                        f"Observation: {obs_json}\n"
                    )
                    if emit_round_progress:
                        await _emit_progress(
                            progress_callback,
                            format_react_text(synthesized),
                        )
                    transcript_parts.append(synthesized)
                    tail = "\n\n".join(transcript_parts).rstrip()
                    full_trace = f"{tail}\n\n最终答案：\n{answer}".strip()
                    logger.debug("ReAct trace (implicit finish):\n{}", full_trace)
                    return ReactRunResult(
                        final_content=answer.strip(),
                        messages=messages,
                        tools_used=tools_used,
                        stop_reason="completed",
                        usage=usage,
                        react_trace=full_trace,
                    )
                obs = (
                    "Observation: 你的上一条回复没有使用 ReAct 三段协议。"
                    "必须严格以下格式重答（每段单独一行，不要加代码围栏、不要用 JSON 对象包裹、不要加 Markdown 粗体）：\n"
                    "Thought: <一句话说明你要做什么>\n"
                    "Action: finish\n"
                    "Observation: \"<直接给出最终答案文本>\"\n"
                    "简单问题（讲笑话、问候、翻译、定义/解释）必须用 Action: finish 一步完成，"
                    "禁止调用工具，禁止自创 tell_joke/generate_joke 这类不存在的工具名，"
                    "禁止用 web_search 传答案。现在请立即按上述格式重新回答用户的原始问题。"
                )
            else:
                naked_plain_text_streak = 0
                obs = (
                    f"Observation: {perr} Please fix your next reply: Thought / Action (function name only) / Observation."
                )
            if emit_round_progress:
                await _emit_progress(progress_callback, obs + "\n")
            transcript_parts.append(obs + "\n")
            messages.append({"role": "user", "content": obs})
            continue
        # Successful parse — reset the naked-text + parse-error streaks.
        naked_plain_text_streak = 0
        consecutive_parse_errors = 0

        assert action is not None
        if action == "finish":
            # Guardrail: if the user asked for a file state change but no
            # state-changing tool ever ran, the "success" observation is
            # almost certainly hallucinated. Reject and force a retry with
            # an explicit correction Observation (up to MAX_STATE_CHANGE_REJECTIONS).
            if (
                state_change_enforceable
                and state_change_rejections < MAX_STATE_CHANGE_REJECTIONS
                and not any(t in REACT_STATE_CHANGE_TOOLS for t in tools_used)
            ):
                state_change_rejections += 1
                available = [
                    n for n in ("write_file", "edit_file", "delete_file", "notebook_edit", "exec")
                    if react_tools.has(n)
                ]
                # Route the "primary" suggestion to the tool that matches the
                # detected intent: deletion → delete_file, otherwise → write_file.
                # Falling through to exec as a last resort only if no file tool
                # is registered at all.
                is_delete_intent = _is_delete_only_intent(_last_user_text)
                if is_delete_intent and "delete_file" in available:
                    primary = "delete_file"
                    example_obs = (
                        "Observation: {\"path\": \"<相对或绝对路径>\"}"
                    )
                elif "write_file" in available:
                    primary = "write_file"
                    example_obs = (
                        "Observation: {\"path\": \"<相对或绝对路径>\", \"content\": \"<要写入的内容>\"}"
                    )
                else:
                    primary = available[0] if available else "write_file"
                    example_obs = "Observation: {<工具参数 JSON>}"

                fallback_hint = (
                    "（如果是删除/清理文件，优先用 delete_file；移动/重命名等复杂操作再用 exec，"
                    "并在 Observation 里写 {\"command\": \"...\"}。）"
                    if "delete_file" in available
                    else (
                        "（如果是删除/移动等操作，请改用 exec 工具并在 Observation 里写 "
                        "{\"command\": \"...\"}。）"
                    )
                )
                obs = (
                    "Observation: 你还没有实际调用任何能真正修改工作区的工具，"
                    "却直接给出了 Action: finish 并在 Observation 中编造了『文件已创建/写入/删除』这类结论。"
                    "这属于幻觉输出，不被允许。用户本轮要求是一个状态改变任务（category B），"
                    f"必须先调用可用工具（优先 {primary}；也可用 {', '.join(available) or 'write_file'}）"
                    "真实执行文件操作，等待真实 Observation 返回成功后，才能再 Action: finish 汇报结果。\n"
                    "请立即以下格式重答（严格三段，不要代码围栏，不要 JSON 对象包裹）：\n"
                    "Thought: <一句话说明你要操作的文件/路径/内容>\n"
                    f"Action: {primary}\n"
                    f"{example_obs}\n"
                    f"{fallback_hint}"
                )
                if emit_round_progress:
                    await _emit_progress(progress_callback, obs + "\n")
                transcript_parts.append(obs + "\n")
                messages.append({"role": "user", "content": obs})
                continue

            answer = finalize_finish_payload(payload)
            tail = "\n\n".join(transcript_parts).rstrip()
            full_trace = f"{tail}\n\n最终答案：\n{answer}".strip()
            logger.debug("ReAct trace:\n{}", full_trace)
            return ReactRunResult(
                final_content=answer.strip(),
                messages=messages,
                tools_used=tools_used,
                stop_reason="completed",
                usage=usage,
                react_trace=full_trace,
            )

        params = normalize_tool_params(payload)
        tool_error = False
        if isinstance(params, str):
            tool_error = True
            obs_text = params
        else:
            tools_used.append(action)
            try:
                result = await react_tools.execute(action, params)
                obs_text = str(result) if result is not None else ""
            except Exception as e:
                logger.exception("ReAct tool execution failed")
                tool_error = True
                obs_text = f"Error executing {action}: {type(e).__name__}: {e}"
            else:
                # ToolRegistry.execute reports missing-tool / invalid-params /
                # known tool failures as a string prefixed with ``Error``
                # (not raised). Treat that as a tool error for streak-counting
                # purposes so repeated failures on the same (non)tool trip
                # the tool_error_limit guard.
                if obs_text.lstrip().startswith("Error"):
                    tool_error = True

        obs_text = truncate_text(obs_text, max_tool_result_chars)
        obs = f"Observation: {obs_text}"
        if emit_round_progress:
            await _emit_progress(progress_callback, obs + "\n")
        transcript_parts.append(obs + "\n")
        messages.append({"role": "user", "content": obs})

        # Consecutive-tool-error guard: if the same tool keeps failing, avoid
        # burning the remaining iteration budget. Reset whenever the model
        # switches tools or a call succeeds — legitimate multi-round tool
        # sequences (e.g. search → fetch → summarize) still work freely.
        if tool_error:
            if last_failing_tool == action:
                consecutive_tool_errors += 1
            else:
                last_failing_tool = action
                consecutive_tool_errors = 1
            if consecutive_tool_errors >= MAX_CONSECUTIVE_TOOL_ERRORS:
                await _maybe_stream_end(resuming=False)
                reason_msg = (
                    f"工具 '{action}' 连续 {consecutive_tool_errors} 次失败，已提前终止循环。"
                    "请检查工具名或参数。"
                )
                if emit_round_progress:
                    await _emit_progress(progress_callback, reason_msg + "\n")
                transcript_parts.append(reason_msg + "\n")
                tail = "\n\n".join(transcript_parts).rstrip()
                logger.warning("ReAct: {}", reason_msg)
                return ReactRunResult(
                    final_content=reason_msg,
                    messages=messages,
                    tools_used=tools_used,
                    stop_reason="tool_error_limit",
                    usage=usage,
                    error=obs_text,
                    react_trace=tail,
                )
        else:
            consecutive_tool_errors = 0
            last_failing_tool = None

    # Exhausted max_iterations without a finish action. Surface a concise
    # termination notice as the user-visible reply; the full round-by-round
    # transcript is preserved in ``react_trace`` (and was already streamed
    # via progress callbacks), so we do not duplicate it into final_content.
    await _maybe_stream_end(resuming=False)
    tail = "\n\n".join(transcript_parts).strip()
    notice = (
        f"达到最大循环次数（{max_iterations} 轮），未收到 Action: finish。"
        "若任务复杂，请增大 max_iterations 或让模型在 Thought 中缩小范围后再 finish。"
    )
    full_trace = f"{tail}\n\n{notice}".strip() if tail else notice
    return ReactRunResult(
        final_content=notice,
        messages=messages,
        tools_used=tools_used,
        stop_reason="max_iterations",
        usage=usage,
        react_trace=full_trace,
    )
