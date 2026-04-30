"""Shared helpers for OneBot v11 payload parsing and verification."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any


def verify_gocqhttp_signature(body: bytes, secret: str, header_value: str | None) -> bool:
    """Validate X-Signature from go-cqhttp post.secret (HMAC-SHA1)."""
    if not secret:
        return True
    if not header_value:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    expected = f"sha1={digest}"
    supplied = header_value.strip().lower()
    # Some relays may strip the "sha1=" prefix; accept either format.
    return hmac.compare_digest(supplied, expected) or hmac.compare_digest(supplied, digest)


def _format_at_segment(data: dict[str, Any]) -> str:
    """Render an OneBot ``at`` segment as ``@<display>``.

    Preference order: the segment's own ``name`` → ``@全体成员`` for ``qq=="all"``
    → the raw QQ number → bare ``@`` as a last resort.
    """
    qq = str(data.get("qq") or "").strip()
    name = str(data.get("name") or "").strip()
    if qq == "all":
        return f"@{name}" if name else "@全体成员"
    if name:
        return f"@{name}"
    if qq:
        return f"@{qq}"
    return "@"


def format_onebot_message_content(message: Any, raw_message: str = "") -> str:
    """Convert OneBot v11 message payload to plain text."""
    if isinstance(message, str):
        return message.strip()

    if isinstance(message, list):
        parts: list[str] = []
        placeholders = {
            "image": "[image]",
            "record": "[audio]",
            "video": "[video]",
            "file": "[file]",
            "reply": "[reply]",
            "face": "[emoji]",
            "json": "[json]",
            "xml": "[xml]",
        }
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "")
            data = seg.get("data") or {}
            if seg_type == "text":
                text = str(data.get("text") or "")
                if text:
                    parts.append(text)
                continue
            if seg_type == "at":
                parts.append(_format_at_segment(data))
                continue
            if seg_type in placeholders:
                parts.append(placeholders[seg_type])
        text = "".join(parts).strip()
        if text:
            return text

    return (raw_message or "").strip()
