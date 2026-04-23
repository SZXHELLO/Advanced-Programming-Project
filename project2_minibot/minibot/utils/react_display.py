"""Terminal display helpers for text ReAct (Thought / Action / Observation)."""

from __future__ import annotations

import re

_HANG = "    "


def format_dialogue_block(role: str, label: str | None, text: str) -> str:
    """Format CLI dialogue with strict ``main agent:`` / ``subagent:`` prefixes.

    Labels appear as ``(label)`` after ``subagent:`` so the prefix never
    contains raw ``[...]`` (which breaks Rich markup).  Continuation lines use
    a hanging indent aligned to the header width.

    Example::

        main agent: I'll delegate file analysis to a subagent.
                    [tool] spawn(...)
        subagent: (readme) Thought: Let me read the file first.
                  [tool] read_file(...)
    """
    if role == "main agent":
        header = "main agent: "
    elif role == "subagent":
        header = "subagent: "
        if label:
            header += f"({label}) "
    else:
        header = f"{role}: "
        if label:
            header += f"({label}) "
    hang = " " * len(header)
    lines = text.split("\n")
    first = header + (lines[0] if lines else "")
    rest = [(hang + ln) if ln.strip() else ln for ln in lines[1:]]
    return "\n".join([first] + rest)


# Section headers at line start (after optional whitespace). Allow optional
# Markdown bold around labels (``**Thought:**``) so CLI layout matches model output.
_SECTION = re.compile(
    r"^\s*\*{0,2}\s*(Thought|Action|Observation|事实对齐)\s*:\s*\*{0,2}\s*(.*)$",
    re.IGNORECASE,
)


def format_react_text(raw: str) -> str:
    """Indent continuation lines under Thought / Action / Observation / 事实对齐.

    Label lines stay flush left; following non-empty lines until the next label
    or round title get a hanging indent so wrapped/续行 align visually.
    """
    if not raw:
        return raw
    lines = raw.split("\n")
    out: list[str] = []
    in_block = False

    for line in lines:
        stripped = line.strip()
        # Round title lines (第n轮循环)
        if _is_round_title(stripped):
            in_block = False
            out.append(stripped)
            continue

        m = _SECTION.match(line)
        if m:
            in_block = True
            first = m.group(1)
            rest = m.group(2)
            if rest:
                out.append(f"{first}: {rest}")
            else:
                out.append(f"{first}:")
            continue

        if in_block and stripped:
            out.append(_HANG + stripped)
        else:
            out.append(line)

    return "\n".join(out)


def _is_round_title(s: str) -> bool:
    return bool(s) and s.startswith("第") and "轮循环" in s[:16]


def buffer_looks_react(buf: str) -> bool:
    """Heuristic: stream buffer is ReAct protocol text (use hang-indent formatting)."""
    if not buf or not buf.strip():
        return False
    if re.search(r"(?m)^Thought:\s", buf):
        return True
    head = buf.lstrip()[:120]
    if "第" in head and "轮循环" in head:
        return True
    return False
