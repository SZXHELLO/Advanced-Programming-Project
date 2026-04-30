"""Text-based ReAct loop (Thought / Action / Observation) without function-calling APIs."""

from __future__ import annotations

import ast
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

from minibot.agent.react_prompt_template import build_react_prompt_from_template
from minibot.agent.tools.registry import ToolRegistry
from minibot.agent.tools.spawn import infer_subagent_responsibilities
from minibot.providers.base import LLMProvider
from minibot.utils.helpers import build_assistant_message, strip_think, truncate_text
from minibot.utils.react_display import format_react_text

# ============================================================================
# Constants and Configuration
# ============================================================================

# User-facing toggle phrases (exact match on stripped message).
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

# Tools excluded in text ReAct mode
REACT_TEXT_EXCLUDE_TOOLS: frozenset[str] = frozenset({"message"})

# State-change and delegation tools
REACT_STATE_CHANGE_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "delete_file", "notebook_edit", "exec"}
)
REACT_FINISH_SATISFYING_TOOLS: frozenset[str] = REACT_STATE_CHANGE_TOOLS | frozenset({"spawn"})

# Safety limits
MAX_CONSECUTIVE_PARSE_ERRORS: int = 3
MAX_CONSECUTIVE_TOOL_ERRORS: int = 3
MAX_STATE_CHANGE_REJECTIONS: int = 2

# Finish action aliases
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

# Error sentinel
NAKED_PLAIN_TEXT_ERROR = "naked plain text: no Thought:/Action:/Observation: labels"

# ============================================================================
# Toggle Detection
# ============================================================================

_TOGGLE_PREFIX_RE = re.compile(r"^(请你?|麻烦|帮我|请帮我|能否|可否|请问)\s*")
_TOGGLE_SUFFIX_RE = re.compile(r"[，,。！？!?\s]+$")
_TOGGLE_TYPO_MAP = (
    ("旅理", "推理"),
    ("retract", "react"),
)


def _normalize_toggle_input(raw: str) -> str:
    """Normalize input for fuzzy toggle matching."""
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
    """Return ``"on"``/``"off"`` when the message is a ReAct toggle intent."""
    if not raw:
        return None
    norm = _normalize_toggle_input(raw)
    if not norm:
        return None

    # 1) Exact match
    for phrase in REACT_TOGGLE_ON_PHRASES:
        if norm == phrase.lower().replace(" ", ""):
            return "on"
    for phrase in REACT_TOGGLE_OFF_PHRASES:
        if norm == phrase.lower().replace(" ", ""):
            return "off"

    # 2) Fuzzy fallback
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


# ============================================================================
# Intent Detection
# ============================================================================

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

_DELETE_VERBS_ZH: tuple[str, ...] = ("删除", "移除", "清除", "清空")
_DELETE_VERBS_EN: tuple[str, ...] = ("delete", "remove", "erase", "unlink")
_WRITE_VERBS_ZH: tuple[str, ...] = (
    "新建", "创建", "建立", "写入", "写到", "写进",
    "保存到", "保存为", "追加到", "追加进",
    "修改", "编辑", "更新", "更改", "替换", "改写", "重命名",
)
_WRITE_VERBS_EN: tuple[str, ...] = (
    "create", "make", "write", "save", "append", "overwrite",
    "modify", "edit", "update", "rename", "replace",
)

_DELEGATION_HINTS: tuple[str, ...] = (
    "spawn",
    "subagent",
    "sub-agent",
    "子agent",
    "子 agent",
    "多agent",
    "多 agent",
    "main agent",
)

_REACT_DELEGATION_SNOOP_ACTIONS: frozenset[str] = frozenset({"glob", "list_dir"})


def detect_file_state_change_intent(text: str) -> bool:
    """Heuristic: does *text* ask the agent to create/modify/delete a file?"""
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


def detect_subagent_delegation_intent(text: str) -> bool:
    """Heuristic: does text explicitly ask for subagent delegation/collaboration?"""
    if not text:
        return False
    s = text.lower()
    if any(h in s for h in _DELEGATION_HINTS):
        return True
    if "agent" in s or "智能体" in text:
        if any(v in text for v in ("调度", "指挥", "委派", "安排", "协调", "支配")):
            return True
        if any(
            v in s
            for v in ("orchestrate", "delegate", "dispatch", "coordinate", "sub-task")
        ):
            return True
    return False


def _is_delete_only_intent(text: str) -> bool:
    """True when *text* asks only for deletion (not create + delete in one turn)."""
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


# ============================================================================
# ReAct Protocol Parsing and Normalization
# ============================================================================

_REACT_LINE_HEADER = re.compile(
    r"^(\s*)\*{0,2}\s*(Thought|Action|Observation)\s*:\s*\*{0,2}\s*(.*)$",
    re.IGNORECASE,
)
_CODE_FENCE_RE = re.compile(
    r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$",
    re.DOTALL,
)
_REACT_LABEL_ANY_RE = re.compile(
    r"^\s*(Thought|Action|Observation)\s*:", re.MULTILINE | re.IGNORECASE
)

_XML_REACT_POSITIONAL: dict[str, tuple[str, ...]] = {
    "write_file": ("path", "content"),
    "edit_file": ("path", "old_text", "new_text"),
    "read_file": ("path",),
    "delete_file": ("path",),
    "list_dir": ("path",),
    "glob": ("pattern", "path"),
    "web_search": ("query",),
    "web_fetch": ("url",),
}


def _looks_like_xml_react(text: str) -> bool:
    """Check if text uses XML-tagged ReAct protocol."""
    low = text.lower()
    return "<action>" in low or "<final_answer>" in low


def _extract_xml_thought(text: str) -> str:
    """Extract thought from XML <thought> tag."""
    m = re.search(r"<thought>\s*(.*?)\s*</thought>", text, flags=re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _strip_enclosing_code_fence(text: str) -> str:
    """Remove a single enclosing ``` code fence (possibly with language tag)."""
    m = _CODE_FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text


def normalize_react_markdown_headers(text: str) -> str:
    """Strip Markdown bold wrappers around ReAct labels (e.g. ``**Thought:**``)."""
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


# ============================================================================
# XML ReAct Parsing
# ============================================================================

def _ast_to_value(node: ast.expr) -> Any:
    """Convert AST expression to Python value."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_ast_to_value(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_ast_to_value(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        out: dict[Any, Any] = {}
        for k, v in zip(node.keys, node.values, strict=False):
            if k is None:
                continue
            out[_ast_to_value(k)] = _ast_to_value(v)  # type: ignore[arg-type]
        return out
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_ast_to_value(node.operand)  # type: ignore[arg-type]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return +_ast_to_value(node.operand)  # type: ignore[arg-type]
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                parts.append(str(_ast_to_value(v.value)))
            else:
                parts.append("")
        return "".join(parts)
    raise ValueError(
        f"不支持的表达式（请使用字面量或在 <action> 中使用关键字参数）: {type(node).__name__}"
    )


def _merge_positional_tool_args(tool: str, args: list[Any]) -> dict[str, Any]:
    """Merge positional arguments into keyword arguments based on tool signature."""
    if not args:
        return {}
    if len(args) == 1 and isinstance(args[0], dict):
        return dict(args[0])
    hint = _XML_REACT_POSITIONAL.get(tool)
    if hint:
        if len(args) > len(hint):
            raise ValueError(
                f"工具 {tool} 至多接受 {len(hint)} 个位置参数；请改用关键字参数。"
            )
        return {hint[i]: args[i] for i in range(len(args))}
    if len(args) == 1 and isinstance(args[0], str):
        return {"query": args[0]}
    raise ValueError(f"工具 {tool} 请使用关键字参数，例如 {tool}(path=\"...\", content=\"...\")")


def _parse_xml_action_invocation_lenient(body: str) -> tuple[str, Any]:
    """Best-effort parser for malformed XML `<action>` calls."""
    m = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([\s\S]*)\)\s*$", body)
    if not m:
        raise ValueError("无法解析 <action>")
    name = m.group(1)
    args_blob = m.group(2)

    if _is_finish_action(name):
        km = re.search(r'answer\s*=\s*"([\s\S]*)"\s*$', args_blob)
        if km:
            return "finish", km.group(1)
        sm = re.search(r'^\s*"([\s\S]*)"\s*$', args_blob)
        if sm:
            return "finish", sm.group(1)
        return "finish", args_blob.strip()

    path_m = re.search(r'path\s*=\s*"([^"]*)"', args_blob)
    content_m = re.search(r'content\s*=\s*"([\s\S]*)"\s*$', args_blob)
    if name == "write_file" and path_m and content_m:
        return name, {"path": path_m.group(1), "content": content_m.group(1)}

    pairs = dict(re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"([^"]*)"', args_blob))
    if pairs:
        return name, pairs
    raise ValueError("无法解析 <action> 参数")


def _parse_xml_action_invocation(body: str) -> tuple[str, Any]:
    """Parse XML action invocation to (tool_name, payload)."""
    body = (body or "").strip()
    if not body:
        raise ValueError("空的 <action>")
    if "(" not in body:
        head = body.split(None, 1)[0].strip()
        if _is_finish_action(head):
            rest = body[len(head) :].strip()
            return "finish", rest
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", body):
            return body, {}
        raise ValueError("无法解析 <action>")

    try:
        tree = ast.parse(body, mode="eval")
    except SyntaxError as e:
        try:
            return _parse_xml_action_invocation_lenient(body)
        except ValueError:
            raise ValueError(str(e)) from e
    
    expr = tree.body
    if isinstance(expr, ast.Name):
        if _is_finish_action(expr.id):
            return "finish", ""
        return expr.id, {}
    if not isinstance(expr, ast.Call):
        raise ValueError("<action> 必须是 工具名(...) 或 finish")

    if not isinstance(expr.func, ast.Name):
        raise ValueError("仅支持简单函数名作为工具名")
    name = expr.func.id
    
    if _is_finish_action(name):
        if not expr.args and not expr.keywords:
            return "finish", ""
        if expr.keywords:
            kw = {k.arg: _ast_to_value(k.value) for k in expr.keywords if k.arg}
            if "answer" in kw:
                return "finish", kw["answer"]
            if len(kw) == 1:
                return "finish", next(iter(kw.values()))
        if expr.args:
            return "finish", _ast_to_value(expr.args[0])
        return "finish", ""

    params: dict[str, Any] = {}
    for kw in expr.keywords:
        if kw.arg:
            params[kw.arg] = _ast_to_value(kw.value)

    pos = [_ast_to_value(a) for a in expr.args]
    if pos:
        merged = _merge_positional_tool_args(name, pos)
        for k, v in merged.items():
            params.setdefault(k, v)
    return name, params


def _try_convert_xml_react_to_line_protocol(text: str) -> str | None:
    """Map XML-tagged ReAct onto Thought:/Action:/Observation: protocol."""
    if not _looks_like_xml_react(text):
        return None
    
    fa = re.search(
        r"<final_answer>\s*(.*?)\s*</final_answer>",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fa:
        thought = _extract_xml_thought(text)
        ans = fa.group(1).strip()
        obs = json.dumps(ans, ensure_ascii=False)
        return f"Thought: {thought}\nAction: finish\nObservation: {obs}"

    am = re.search(
        r"<action>\s*(.*?)\s*</action>",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not am:
        return None
    
    try:
        tool_name, payload = _parse_xml_action_invocation(am.group(1).strip())
    except ValueError as e:
        logger.debug("ReAct XML <action> parse failed: {}", e)
        return None

    thought = _extract_xml_thought(text)
    if tool_name == "finish":
        obs_val: Any = payload
        if isinstance(obs_val, str):
            obs = json.dumps(obs_val, ensure_ascii=False)
        else:
            obs = json.dumps(obs_val, ensure_ascii=False)
        return f"Thought: {thought}\nAction: finish\nObservation: {obs}"

    if not isinstance(payload, dict):
        payload = {} if payload is None else {"_": payload}
    obs_line = json.dumps(payload, ensure_ascii=False)
    return f"Thought: {thought}\nAction: {tool_name}\nObservation: {obs_line}"


# ============================================================================
# DSML Protocol Handling
# ============================================================================

_DSML_INVOKE_OPEN_RE = re.compile(
    r"<[^>]*\binvoke\b[^>]*\bname\s*=\s*\"([^\"]+)\"[^>]*>",
    re.IGNORECASE,
)
_DSML_PARAM_RE = re.compile(
    r"<[^>]*\bparameter\b[^>]*\bname\s*=\s*\"([^\"]+)\"[^>]*>([^<]*)</[^>]*\bparameter\b[^>]*>",
    re.IGNORECASE,
)
_DSML_INVOKE_CLOSE_RE = re.compile(r"</[^>]*\binvoke\b[^>]*>", re.IGNORECASE)


def _coerce_dsml_scalar(val: str) -> Any:
    """Coerce DSML parameter text to bool / int / float when obvious, else str."""
    v = (val or "").strip()
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low.isdigit() or (low.startswith("-") and low[1:].isdigit()):
        try:
            return int(v, 10)
        except ValueError:
            pass
    try:
        if "." in v:
            return float(v)
    except ValueError:
        pass
    return v


def _extract_dsml_invokes(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse DeepSeek-style DSML ``invoke`` blocks into (tool_name, params dict)."""
    out: list[tuple[str, dict[str, Any]]] = []
    for m in _DSML_INVOKE_OPEN_RE.finditer(text):
        name = (m.group(1) or "").strip()
        if not name:
            continue
        rest = text[m.end() :]
        close = _DSML_INVOKE_CLOSE_RE.search(rest)
        chunk = rest[: close.start()] if close else rest
        params: dict[str, Any] = {}
        for pm in _DSML_PARAM_RE.finditer(chunk):
            k = (pm.group(1) or "").strip()
            if k:
                params[k] = _coerce_dsml_scalar(pm.group(2) or "")
        out.append((name, params))
    return out


def _convert_dsml_assistant_blob_to_react(raw: str) -> str | None:
    """Convert DSML tool markup to ReAct protocol if no ReAct labels present."""
    s = (raw or "").strip()
    if not s:
        return None
    if _REACT_LABEL_ANY_RE.search(s):
        return None
    low = s.lower()
    if "invoke" not in low and "dsml" not in low:
        return None
    invokes = _extract_dsml_invokes(s)
    if not invokes:
        return None
    if len(invokes) > 1:
        logger.debug(
            "ReAct: DSML contained {} invokes; using first only ({})",
            len(invokes),
            invokes[0][0],
        )
    tool_name, params = invokes[0]
    thought = "模型输出了 DSML/类 function-calling 标记；已转换为 ReAct 协议单行工具调用。"
    return (
        f"Thought: {thought}\n"
        f"Action: {tool_name}\n"
        f"Observation: {json.dumps(params, ensure_ascii=False)}"
    )


def _try_unfold_react_json_object(text: str) -> str | None:
    """Unfold a JSON object using ReAct keys into canonical protocol lines."""
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


def preprocess_react_model_output(text: str) -> str:
    """Best-effort normalization of common LLM formatting deviations."""
    raw = (text or "").strip()
    if not raw:
        return raw
    
    # Try XML ReAct conversion first
    xml_mapped = _try_convert_xml_react_to_line_protocol(raw)
    if xml_mapped is not None:
        raw = xml_mapped
    elif not _looks_like_xml_react(raw):
        raw = strip_think(raw)
    
    if not raw:
        return raw
    
    # Strip code fences
    raw = _strip_enclosing_code_fence(raw)
    
    # Try JSON object unfolding
    unfolded = _try_unfold_react_json_object(raw)
    if unfolded is not None:
        raw = unfolded
    
    # Try DSML conversion
    dsml = _convert_dsml_assistant_blob_to_react(raw)
    if dsml is not None:
        raw = dsml
    
    # Normalize markdown headers
    raw = normalize_react_markdown_headers(raw)
    return raw


# ============================================================================
# ReAct Step Parsing
# ============================================================================

def _is_finish_action(action_name: str) -> bool:
    """True when *action_name* is any recognized synonym for ``finish``."""
    if not action_name:
        return False
    key = action_name.strip().lower().replace("-", "_").replace(" ", "_")
    return key in FINISH_ACTION_ALIASES


def _is_naked_plain_answer(text: str) -> bool:
    """True when the model emitted a non-empty reply with *no* ReAct labels."""
    if not text.strip():
        return False
    if _looks_like_xml_react(text):
        return False
    return _REACT_LABEL_ANY_RE.search(text) is None


def _extract_observation_blob(raw: str) -> str | None:
    """Prefer `Observation:`; fall back to legacy `Action Input:` for compatibility."""
    for pattern in (r"^Observation:\s*", r"^Action Input:\s*"):
        m = re.search(pattern, raw, flags=re.MULTILINE)
        if m:
            return raw[m.end() :].strip()
    return None


def _parse_observation_payload(blob: str) -> tuple[Any | None, str | None, str | None]:
    """Parse Observation body: first line (or whole blob) JSON + optional alignment text."""
    blob_stripped = blob.strip()
    if not blob_stripped:
        return None, None, "empty Observation"

    # Try whole blob as JSON
    try:
        return json.loads(blob_stripped), None, None
    except json.JSONDecodeError:
        pass

    # Try first line as JSON
    parts = blob_stripped.split("\n", 1)
    first = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    try:
        val = json.loads(first)
        alignment = rest if rest else None
        return val, alignment, None
    except json.JSONDecodeError:
        pass

    # Fall back to plain string
    return blob_stripped, None, None


def parse_react_step(
    text: str,
) -> tuple[str | None, str | None, Any | None, str | None, str | None]:
    """Parse one ReAct step.

    Returns:
        (thought, action, payload, alignment_text, error)
        
    When the model reply contains **no** ReAct labels at all, returns the
    sentinel :data:`NAKED_PLAIN_TEXT_ERROR` as the error.
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


# ============================================================================
# Helper Functions
# ============================================================================

_CN_NUM = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")


def cycle_title(n: int) -> str:
    """Format round number as '第 N 轮循环'."""
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


def _extract_last_user_text(messages: list[dict[str, Any]]) -> str:
    """Return the plain-text payload of the most recent user turn."""
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


def _build_current_task_anchor(last_user_text: str) -> str:
    """Create a strict per-turn anchor so the model focuses on latest user text."""
    task = (last_user_text or "").strip()
    if not task:
        task = "（空）"
    return (
        "## Current task anchor (must follow)\n"
        "The **only** task for this run is the latest user message below. "
        "Do not answer any earlier question unless the latest message explicitly asks to revisit it.\n"
        "本轮只允许回答下面这条"最新用户消息"；不要回答上一题。\n\n"
        f"最新用户消息：\n{task}"
    )


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
    """Build ReAct protocol appendix with tool catalog."""
    catalog = format_tool_catalog(tools.get_definitions())
    return build_react_prompt_from_template(tool_list=catalog)


def inject_react_instructions(
    messages: list[dict[str, Any]],
    tools: ToolRegistry,
) -> list[dict[str, Any]]:
    """Inject ReAct protocol into messages."""
    out = [dict(m) for m in messages]
    _append_react_protocol(out, build_react_appendix(tools))
    return out


# ============================================================================
# Main Loop
# ============================================================================

@dataclass
class ReactRunResult:
    """Result of a ReAct loop execution.
    
    Attributes:
        final_content: User-visible reply (answer text only on success)
        messages: Full conversation history
        tools_used: List of tool names used during execution
        stop_reason: How the loop terminated (completed/max_iterations/error/etc.)
        usage: Token usage statistics
        error: Error message if applicable
        react_trace: Full ReAct trace for logging
    """
    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    stop_reason: str = "completed"
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    react_trace: str | None = None


async def _emit_progress(
    progress_callback: Callable[..., Any] | None,
    text: str,
) -> None:
    """Helper to emit progress callback (sync or async)."""
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

    Args:
        provider: LLM provider for completions
        tools: Tool registry with available tools
        initial_messages: Starting conversation context
        model: Model identifier
        max_iterations: Maximum number of rounds
        max_tool_result_chars: Truncation limit for tool results
        provider_retry_mode: Retry strategy for LLM calls
        progress_callback: Optional callback for progress updates
        on_stream_delta: Optional callback for streaming content deltas
        on_stream_end: Optional callback when streaming finishes
        exclude_tools: Tools to exclude from this loop
        emit_round_progress: Whether to emit per-round progress

    Returns:
        ReactRunResult with final answer and execution details

    The loop stops on:
    1. Action: finish (or aliases) → stop_reason='completed'
    2. max_iterations reached → stop_reason='max_iterations'
    3. Provider error → stop_reason='error'
    4. Consecutive parse errors → stop_reason='parse_error_limit'
    5. Consecutive tool errors → stop_reason='tool_error_limit'
    """
    # Setup
    exclude = exclude_tools if exclude_tools is not None else REACT_TEXT_EXCLUDE_TOOLS
    react_tools = tools.copy_excluding(exclude)
    messages = inject_react_instructions([dict(m) for m in initial_messages], react_tools)
    tools_used: list[str] = []
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    transcript_parts: list[str] = []
    
    # Safety counters
    naked_plain_text_streak = 0
    last_naked_plain_text = ""
    consecutive_parse_errors = 0
    consecutive_tool_errors = 0
    last_failing_tool: str | None = None

    # Intent detection and guardrails
    _last_user_text = _extract_last_user_text(initial_messages)
    _current_task_anchor = _build_current_task_anchor(_last_user_text)
    messages.append({"role": "user", "content": _current_task_anchor})
    
    state_change_intent = detect_file_state_change_intent(_last_user_text)
    delegation_intent = detect_subagent_delegation_intent(_last_user_text)
    state_change_enforceable = state_change_intent and any(
        react_tools.has(name) for name in REACT_STATE_CHANGE_TOOLS
    )
    state_change_rejections = 0

    def _acc_usage(u: dict[str, int] | None) -> None:
        """Accumulate token usage statistics."""
        if not u:
            return
        for k, v in u.items():
            try:
                usage[k] = usage.get(k, 0) + int(v)
            except (TypeError, ValueError):
                continue

    use_llm_stream = on_stream_delta is not None

    async def _maybe_stream_end(*, resuming: bool) -> None:
        """Helper to call stream end callback if configured."""
        if not use_llm_stream or on_stream_end is None:
            return
        r = on_stream_end(resuming=resuming)
        if inspect.isawaitable(r):
            await r

    # Main loop
    for round_idx in range(1, max_iterations + 1):
        _round_n = round_idx
        stream_round_title_sent = False

        async def _on_content_delta(delta: str) -> None:
            """Stream delta callback that injects round title on first chunk."""
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

        # Call LLM
        logger.debug("ReAct round {}/{}: calling LLM", round_idx, max_iterations)
        response = await provider.chat_stream_with_retry(
            messages=messages,
            tools=None,
            model=model,
            retry_mode=provider_retry_mode,
            on_content_delta=_on_content_delta if use_llm_stream else None,
        )
        _acc_usage(response.usage)

        # Handle provider errors
        if response.finish_reason == "error":
            err = response.content or "model error"
            logger.error("ReAct: model error in round {}: {}", round_idx, err[:200])
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

        raw_text = (response.content or "").strip()
        if response.has_tool_calls:
            logger.warning("ReAct: model returned tool_calls despite tools=None; using text only")

        # Parse ReAct step
        thought, action, payload, alignment_text, perr = parse_react_step(raw_text)
        effective_raw = raw_text
        
        # Delegation intent upgrade (round 1 only)
        if (
            not perr
            and delegation_intent
            and round_idx == 1
            and action in _REACT_DELEGATION_SNOOP_ACTIONS
        ):
            sniff_action = action
            spawn_payload = {
                "task": _last_user_text.strip() or "Complete the delegated multi-agent work.",
                "label": "orchestration",
            }
            upgraded = (
                "Thought: 用户要求指挥或调度子 agent；使用 spawn 委派完整任务，"
                "而不是仅做 workspace 目录浏览。\n"
                f"Action: spawn\n"
                f"Observation: {json.dumps(spawn_payload, ensure_ascii=False)}"
            )
            t2, a2, p2, al2, e2 = parse_react_step(upgraded)
            if not e2:
                thought, action, payload, alignment_text, perr = t2, a2, p2, al2, e2
                effective_raw = upgraded
                logger.info(
                    "ReAct: upgraded round-1 tool '{}' to spawn (delegation intent)",
                    sniff_action,
                )

        # Format and emit round header
        header = f"{cycle_title(round_idx)}\n{effective_raw.strip()}\n"
        if use_llm_stream and on_stream_end is not None:
            if perr or action != "finish":
                await _maybe_stream_end(resuming=True)
            else:
                await _maybe_stream_end(resuming=False)

        if emit_round_progress:
            if not use_llm_stream:
                await _emit_progress(progress_callback, format_react_text(header))
            if alignment_text:
                await _emit_progress(progress_callback, f"事实对齐: {alignment_text}\n")
        
        transcript_parts.append(header)
        if alignment_text:
            transcript_parts.append(f"事实对齐: {alignment_text}\n")

        # Add to conversation history
        messages.append(
            build_assistant_message(
                effective_raw,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )
        )

        # Handle parse errors
        if perr:
            consecutive_parse_errors += 1
            
            # Check parse error limit
            if consecutive_parse_errors >= MAX_CONSECUTIVE_PARSE_ERRORS:
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
            
            # Handle naked plain text
            if perr == NAKED_PLAIN_TEXT_ERROR:
                naked_plain_text_streak += 1
                last_naked_plain_text = raw_text.strip()
                
                # After 2 consecutive naked replies, synthesize finish
                if (
                    naked_plain_text_streak >= 2
                    and last_naked_plain_text
                    and not state_change_enforceable
                    and not delegation_intent
                ):
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
                        await _emit_progress(progress_callback, format_react_text(synthesized))
                    transcript_parts.append(synthesized)
                    tail = "\n\n".join(transcript_parts).rstrip()
                    full_trace = f"{tail}\n\n最终答案：\n{answer}".strip()
                    logger.debug("ReAct: implicit finish after {} naked replies", naked_plain_text_streak)
                    return ReactRunResult(
                        final_content=answer.strip(),
                        messages=messages,
                        tools_used=tools_used,
                        stop_reason="completed",
                        usage=usage,
                        react_trace=full_trace,
                    )
                
                # Send correction observation
                obs = (
                    "Observation: 你的上一条回复没有使用 ReAct 三段协议。"
                    "必须严格以下格式重答（每段单独一行，不要加代码围栏、不要用 JSON 对象包裹、不要加 Markdown 粗体）：\n"
                    "Thought: <一句话说明你要做什么>\n"
                    f"Action: {'spawn' if delegation_intent else 'finish'}\n"
                    f"Observation: {'{\"task\": \"<委派给子agent的任务说明>\"}' if delegation_intent else '\"<直接给出最终答案文本>\"'}\n"
                    "简单问题（讲笑话、问候、翻译、定义/解释）必须用 Action: finish 一步完成，"
                    "禁止调用工具，禁止自创 tell_joke/generate_joke 这类不存在的工具名，"
                    "禁止用 web_search 传答案。现在请立即按上述格式重新回答下面这条最新用户消息，不要回答上一题：\n"
                    f"{_last_user_text.strip()}"
                )
            else:
                naked_plain_text_streak = 0
                obs = (
                    f"Observation: {perr} Please fix your next reply: "
                    "Thought / Action (function name only) / Observation."
                )
            
            if emit_round_progress:
                await _emit_progress(progress_callback, obs + "\n")
            transcript_parts.append(obs + "\n")
            messages.append({"role": "user", "content": obs})
            continue

        # Successful parse - reset error counters
        naked_plain_text_streak = 0
        consecutive_parse_errors = 0

        assert action is not None
        
        # Handle finish action
        if action == "finish":
            # State change guardrail
            if (
                state_change_enforceable
                and state_change_rejections < MAX_STATE_CHANGE_REJECTIONS
                and not any(t in REACT_FINISH_SATISFYING_TOOLS for t in tools_used)
            ):
                state_change_rejections += 1
                available = [
                    n for n in ("write_file", "edit_file", "delete_file", "notebook_edit", "exec")
                    if react_tools.has(n)
                ]
                
                # Choose primary tool based on intent
                is_delete_intent = _is_delete_only_intent(_last_user_text)
                if is_delete_intent and "delete_file" in available:
                    primary = "delete_file"
                    example_obs = "Observation: {\"path\": \"<相对或绝对路径>\"}"
                elif "write_file" in available:
                    primary = "write_file"
                    example_obs = "Observation: {\"path\": \"<相对或绝对路径>\", \"content\": \"<要写入的内容>\"}"
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
                logger.info("ReAct: rejected hallucinated finish (rejection {})", state_change_rejections)
                if emit_round_progress:
                    await _emit_progress(progress_callback, obs + "\n")
                transcript_parts.append(obs + "\n")
                messages.append({"role": "user", "content": obs})
                continue

            # Valid finish - extract answer and return
            answer = finalize_finish_payload(payload)
            tail = "\n\n".join(transcript_parts).rstrip()
            full_trace = f"{tail}\n\n最终答案：\n{answer}".strip()
            logger.debug("ReAct: completed in {} rounds", round_idx)
            return ReactRunResult(
                final_content=answer.strip(),
                messages=messages,
                tools_used=tools_used,
                stop_reason="completed",
                usage=usage,
                react_trace=full_trace,
            )

        # Execute tool
        params = normalize_tool_params(payload)
        tool_error = False
        
        if isinstance(params, str):
            # Parameter normalization error
            tool_error = True
            obs_text = params
            logger.warning("ReAct: tool parameter error: {}", params[:200])
        else:
            tools_used.append(action)
            logger.info("ReAct: executing tool {}({})", action, json.dumps(params, ensure_ascii=False)[:200])
            
            try:
                result = await react_tools.execute(action, params)
                obs_text = str(result) if result is not None else ""
                logger.debug("ReAct: tool {} returned {} chars", action, len(obs_text))
            except Exception as e:
                logger.exception("ReAct: tool {} execution failed", action)
                tool_error = True
                obs_text = f"Error executing {action}: {type(e).__name__}: {e}"
            else:
                # Check for tool-reported errors
                if obs_text.lstrip().startswith("Error"):
                    tool_error = True
                    logger.warning("ReAct: tool {} reported error: {}", action, obs_text[:200])
                elif action == "spawn" and not tool_error:
                    # Special handling for spawn tool
                    task_text = ""
                    if isinstance(params, dict):
                        task_text = str(params.get("task") or params.get("command") or "")
                    roles = infer_subagent_responsibilities(task_text)
                    action_plan = (
                        "Action: spawn\n"
                        f"创建子agent: {params.get('label') if isinstance(params, dict) and params.get('label') else roles[0]}\n"
                        f"职责分工: {', '.join(roles)}"
                    )
                    if emit_round_progress:
                        await _emit_progress(progress_callback, format_react_text(action_plan) + "\n")
                    transcript_parts.append(action_plan + "\n")
                    obs_text = (
                        obs_text
                        + "\n\n[Runtime] 子智能体已在后台运行。下一轮请直接 Action: finish，"
                        "简要说明已委派；不要再次 spawn，不要执行与当前用户请求无关的 delete/exec。"
                    )
                    logger.info("ReAct: spawned subagent with roles: {}", roles)

        # Truncate and format observation
        obs_text = truncate_text(obs_text, max_tool_result_chars)
        obs = f"Observation: {obs_text}"
        if emit_round_progress:
            await _emit_progress(progress_callback, obs + "\n")
        transcript_parts.append(obs + "\n")
        messages.append({"role": "user", "content": obs})

        # Tool error handling
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

    # Max iterations reached
    await _maybe_stream_end(resuming=False)
    tail = "\n\n".join(transcript_parts).strip()
    notice = (
        f"达到最大循环次数（{max_iterations} 轮），未收到 Action: finish。"
        "若任务复杂，请增大 max_iterations 或让模型在 Thought 中缩小范围后再 finish。"
    )
    full_trace = f"{tail}\n\n{notice}".strip() if tail else notice
    logger.warning("ReAct: max iterations ({}) reached", max_iterations)
    return ReactRunResult(
        final_content=notice,
        messages=messages,
        tools_used=tools_used,
        stop_reason="max_iterations",
        usage=usage,
        react_trace=full_trace,
    )