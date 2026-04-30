"""QQ mention collector channel for go-cqhttp reverse HTTP webhook."""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import aiohttp
from aiohttp import web
from loguru import logger

from minibot.bus.events import OutboundMessage
from minibot.bus.queue import MessageBus
from minibot.channels._onebot_utils import (
    format_onebot_message_content,
    verify_gocqhttp_signature,
)
from minibot.channels.base import BaseChannel
from minibot.config.loader import load_config
from minibot.config.paths import get_workspace_path
from minibot.config.schema import QQCollectorConfig
from minibot.utils.helpers import ensure_dir

_CQ_AT_RE = re.compile(r"\[CQ:at,qq=([^,\]]+)")
# Fallback regex for detecting literal "@all" in plain text (word-bounded to
# avoid false positives like "@allergy"). "@全体成员" is matched as a raw
# substring because it already has a natural word boundary.
_TEXT_AT_ALL_EN_RE = re.compile(r"@all\b", re.IGNORECASE)
_TEXT_AT_ALL_ZH = "@全体成员"


def _extract_mention_kind(
    payload: dict[str, Any],
    *,
    match_at_me: bool,
    match_at_all: bool,
    text_mention_aliases: list[str] | None = None,
) -> Literal["me", "all"] | None:
    """Return mention kind from OneBot payload, or None if no target mention.

    Detection order:
    1. Real OneBot at-segments (preferred, produced when users select the
       mention from QQ's popup).
    2. CQ-style at-segments embedded in a string ``message``.
    3. Literal ``@<alias>`` / ``@<self_id>`` text fallback – covers users who
       type ``@BotName`` manually instead of using the @ popup.
    """
    message = payload.get("message")
    self_id = str(payload.get("self_id") or "").strip()
    has_at_all = False
    has_at_me = False

    text_chunks: list[str] = []
    raw_message = str(payload.get("raw_message") or "")
    if raw_message:
        text_chunks.append(raw_message)

    if isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict):
                continue
            seg_type = str(segment.get("type") or "")
            data = segment.get("data") or {}
            if seg_type == "at":
                qq = str(data.get("qq") or "").strip()
                if qq == "all":
                    has_at_all = True
                if self_id and qq == self_id:
                    has_at_me = True
            elif seg_type == "text":
                text_chunks.append(str(data.get("text") or ""))
    elif isinstance(message, str):
        for match in _CQ_AT_RE.finditer(message):
            qq = match.group(1).strip()
            if qq == "all":
                has_at_all = True
            if self_id and qq == self_id:
                has_at_me = True
        text_chunks.append(message)

    combined_text = "\n".join(chunk for chunk in text_chunks if chunk)

    if not has_at_me and combined_text:
        me_aliases: list[str] = []
        if self_id:
            me_aliases.append(self_id)
        if text_mention_aliases:
            me_aliases.extend(a.strip() for a in text_mention_aliases if a and a.strip())
        for alias in me_aliases:
            if f"@{alias}" in combined_text:
                has_at_me = True
                break

    if not has_at_all and combined_text:
        if _TEXT_AT_ALL_ZH in combined_text or _TEXT_AT_ALL_EN_RE.search(combined_text):
            has_at_all = True

    if match_at_all and has_at_all:
        return "all"
    if match_at_me and has_at_me:
        return "me"
    return None


def _sender_display(payload: dict[str, Any]) -> str:
    sender = payload.get("sender")
    if isinstance(sender, dict):
        card = str(sender.get("card") or "").strip()
        nickname = str(sender.get("nickname") or "").strip()
        if card:
            return card
        if nickname:
            return nickname
    return str(payload.get("user_id") or "").strip() or "unknown"


class QQCollectorChannel(BaseChannel):
    """QQ read-only collector channel that writes @mentions into Markdown."""

    name = "qq_collector"
    display_name = "QQ Mention Collector"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = QQCollectorConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: QQCollectorConfig = config
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._output_base: Path | None = None
        self._http: aiohttp.ClientSession | None = None
        # Auto-discovered bot identity — populated from NapCat OneBot API on
        # startup / first group event. Empty string means "unknown / not yet fetched".
        self._bot_nickname: str = ""
        # Generic per-(group_id, user_id) display-name cache used both for the
        # bot's own card and for any @-mentioned member encountered in events.
        # An empty-string value means "already tried, nothing useful returned";
        # we don't retry until the channel restarts.
        self._group_member_cards: dict[tuple[str, str], str] = {}

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return QQCollectorConfig().model_dump(by_alias=True)

    def _resolve_output_base(self) -> Path:
        if self._output_base is not None:
            return self._output_base
        if self.config.output_dir.strip():
            base = ensure_dir(Path(self.config.output_dir).expanduser())
        else:
            cfg = load_config()
            base = ensure_dir(get_workspace_path(cfg.agents.defaults.workspace) / "QQInfo")
        self._output_base = base
        return base

    async def start(self) -> None:
        self._running = True
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

        app = web.Application()
        app.router.add_post(self.config.path, self._handle_webhook)
        app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await self._site.start()
        logger.info(
            "QQ collector listening on http://{}:{}{}",
            self.config.host,
            self.config.port,
            self.config.path,
        )

        # Best-effort: auto-discover the bot's global nickname right away so the
        # very first incoming event can already match against it.
        if self.config.auto_fetch_bot_name:
            await self._refresh_bot_nickname()

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def _call_onebot_api(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """POST an OneBot HTTP action and return ``data`` on success, else None."""
        if self._http is None:
            return None
        api_base = self.config.api_base.strip().rstrip("/")
        if not api_base:
            return None
        url = f"{api_base}/{endpoint.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        token = self.config.access_token.strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with self._http.post(url, json=params or {}, headers=headers) as resp:
                if resp.status >= 400:
                    logger.debug(
                        "qq_collector: OneBot {} returned HTTP {}", endpoint, resp.status
                    )
                    return None
                body = await resp.json(content_type=None)
        except Exception as e:
            logger.debug("qq_collector: OneBot {} failed: {}", endpoint, e)
            return None
        if not isinstance(body, dict):
            return None
        if str(body.get("status") or "").lower() not in {"ok", "async"} and body.get("retcode") != 0:
            logger.debug(
                "qq_collector: OneBot {} non-ok status={} retcode={}",
                endpoint,
                body.get("status"),
                body.get("retcode"),
            )
            return None
        data = body.get("data")
        return data if isinstance(data, dict) else None

    async def _refresh_bot_nickname(self) -> None:
        """Fetch the bot's global nickname via `get_login_info` (non-fatal)."""
        data = await self._call_onebot_api("get_login_info")
        if not data:
            return
        nick = str(data.get("nickname") or "").strip()
        if nick and nick != self._bot_nickname:
            self._bot_nickname = nick
            logger.info("qq_collector: auto-detected bot nickname '{}'", nick)

    async def _resolve_group_member_display(self, group_id: str, user_id: str) -> str:
        """Return the display name for *user_id* in *group_id*; cached per run.

        Tries the group card first, falls back to the member's nickname. An
        empty string is cached on failure to avoid thrashing the API.
        """
        if not group_id or not user_id:
            return ""
        key = (group_id, user_id)
        if key in self._group_member_cards:
            return self._group_member_cards[key]

        def _maybe_int(value: str) -> Any:
            return int(value) if value.isdigit() else value

        data = await self._call_onebot_api(
            "get_group_member_info",
            {
                "group_id": _maybe_int(group_id),
                "user_id": _maybe_int(user_id),
                "no_cache": False,
            },
        )
        display = ""
        if data:
            display = str(data.get("card") or "").strip() or str(data.get("nickname") or "").strip()
        self._group_member_cards[key] = display
        return display

    async def _resolve_group_card(self, group_id: str, self_id: str) -> str:
        """Return the bot's card in *group_id*; cached after first call."""
        if not self.config.auto_fetch_bot_name:
            return ""
        key = (group_id, self_id)
        first_time = key not in self._group_member_cards
        card = await self._resolve_group_member_display(group_id, self_id)
        if first_time and card:
            logger.info(
                "qq_collector: auto-detected bot card '{}' for group {}", card, group_id
            )
        return card

    async def _resolve_me_aliases(self, group_id: str, self_id: str) -> list[str]:
        """Build the @me alias list for this event (bot names + user config)."""
        aliases: list[str] = []
        if self.config.auto_fetch_bot_name:
            # Nickname may be empty if startup fetch failed — try once more lazily.
            if not self._bot_nickname:
                await self._refresh_bot_nickname()
            if self._bot_nickname:
                aliases.append(self._bot_nickname)
            if group_id and self_id:
                card = await self._resolve_group_card(group_id, self_id)
                if card and card not in aliases:
                    aliases.append(card)
        for extra in self.config.text_mention_aliases or []:
            extra = str(extra).strip()
            if extra and extra not in aliases:
                aliases.append(extra)
        return aliases

    async def _enrich_at_names(
        self, payload: dict[str, Any], group_id: str, self_id: str
    ) -> None:
        """Populate ``data.name`` on at-segments in-place so the markdown shows
        ``@<display>`` instead of a bare ``@``.

        The resolution order per at-segment:

        * ``qq == "all"``              → ``全体成员``
        * ``qq == self_id``            → bot's group card / global nickname
        * other users                  → group member card via OneBot API (cached)

        Any pre-existing ``name`` field is left untouched.
        """
        message = payload.get("message")
        if not isinstance(message, list):
            return

        for seg in message:
            if not isinstance(seg, dict) or str(seg.get("type") or "") != "at":
                continue
            data = seg.get("data")
            if not isinstance(data, dict):
                data = {}
                seg["data"] = data
            if str(data.get("name") or "").strip():
                continue

            qq = str(data.get("qq") or "").strip()
            if not qq:
                continue
            if qq == "all":
                data["name"] = "全体成员"
                continue

            display = ""
            if qq == self_id:
                # Prefer the group card (matches what others see in this group),
                # then fall back to the global nickname fetched at startup.
                display = await self._resolve_group_card(group_id, self_id)
                if not display:
                    display = self._bot_nickname
            else:
                display = await self._resolve_group_member_display(group_id, qq)

            if display:
                data["name"] = display
            else:
                # Last resort: show the QQ number so the reader still sees who was
                # mentioned instead of a bare "@".
                data["name"] = qq

    async def send(self, msg: OutboundMessage) -> None:
        """Collector channel is read-only and never sends outbound messages."""
        logger.debug("qq_collector ignores outbound message for {}", msg.chat_id)

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.read()
        signature = request.headers.get("X-Signature") or request.headers.get("x-signature")
        if not verify_gocqhttp_signature(body, self.config.secret.strip(), signature):
            logger.warning("qq_collector: signature verification failed")
            return web.json_response({"error": "invalid signature"}, status=401)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            logger.warning("qq_collector: invalid json body")
            return web.json_response({"error": "invalid json"}, status=400)

        logger.debug(
            "qq_collector received event post_type={} message_type={} group_id={} self_id={}",
            payload.get("post_type"),
            payload.get("message_type"),
            payload.get("group_id"),
            payload.get("self_id"),
        )

        try:
            await self._handle_event(payload)
        except Exception:
            logger.exception("qq_collector: failed to process event")
            return web.json_response({"error": "internal error"}, status=500)
        return web.json_response({"ok": True})

    async def _handle_event(self, payload: dict[str, Any]) -> None:
        post_type = str(payload.get("post_type") or "")
        if post_type != "message":
            logger.debug("qq_collector: skip non-message event post_type={}", post_type)
            return
        message_type = str(payload.get("message_type") or "").lower()
        if message_type != "group":
            logger.debug("qq_collector: skip non-group message message_type={}", message_type)
            return

        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            logger.debug("qq_collector: skip group message without group_id")
            return

        allow_groups = {str(item).strip() for item in self.config.allow_groups if str(item).strip()}
        if allow_groups and group_id not in allow_groups:
            logger.debug(
                "qq_collector: skip group {} (not in allow_groups {})",
                group_id,
                sorted(allow_groups),
            )
            return

        self_id = str(payload.get("self_id") or "").strip()
        me_aliases = await self._resolve_me_aliases(group_id, self_id)

        mention_kind = _extract_mention_kind(
            payload,
            match_at_me=self.config.match_at_me,
            match_at_all=self.config.match_at_all,
            text_mention_aliases=me_aliases,
        )
        if mention_kind is None and not self.config.include_plain_group_msgs:
            logger.debug(
                "qq_collector: skip group {} (no @me/@all mention; match_at_me={} match_at_all={} "
                "self_id={} aliases={}) raw_message={!r} message={!r}",
                group_id,
                self.config.match_at_me,
                self.config.match_at_all,
                self_id,
                me_aliases,
                payload.get("raw_message"),
                payload.get("message"),
            )
            return

        # Fill in missing display names on at-segments so the rendered markdown
        # reads "@HelloAgent" instead of a bare "@" when the user used QQ's
        # real @-popup (which doesn't always carry the `name` field).
        await self._enrich_at_names(payload, group_id, self_id)

        ts = payload.get("time")
        dt = datetime.fromtimestamp(int(ts)) if ts else datetime.now()
        timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
        text = format_onebot_message_content(payload.get("message"), str(payload.get("raw_message") or ""))
        sender_id = str(payload.get("user_id") or "").strip() or "unknown"
        sender_name = _sender_display(payload)
        msg_id = str(payload.get("message_id") or "").strip() or "unknown"
        tag = mention_kind or "plain"

        await self._append_markdown(
            group_id=group_id,
            timestamp=timestamp,
            sender_name=sender_name,
            sender_id=sender_id,
            mention_tag=tag,
            message_id=msg_id,
            content=text or "[empty]",
        )

    async def _append_markdown(
        self,
        *,
        group_id: str,
        timestamp: str,
        sender_name: str,
        sender_id: str,
        mention_tag: str,
        message_id: str,
        content: str,
    ) -> None:
        lock = self._locks[group_id]
        async with lock:
            output_base = self._resolve_output_base()
            file_path = output_base / f"group_{group_id}.md"
            if not file_path.exists():
                file_path.write_text(f"# QQ 群 {group_id}\n\n", encoding="utf-8")
            block = (
                f"## {timestamp} — {sender_name} ({sender_id}) [@{mention_tag} | msg_id={message_id}]\n\n"
                f"{content.strip() or '[empty]'}\n\n"
            )
            with open(file_path, "a", encoding="utf-8") as fp:
                fp.write(block)
            logger.info(
                "qq_collector: recorded @{} mention from {} ({}) in group {} -> {}",
                mention_tag,
                sender_name,
                sender_id,
                group_id,
                file_path,
            )
