"""QQ channel for go-cqhttp reverse HTTP webhook (OneBot v11)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import aiohttp
from aiohttp import web
from loguru import logger
from pydantic import Field, field_validator

from minibot.bus.events import OutboundMessage
from minibot.bus.queue import MessageBus
from minibot.channels.base import BaseChannel
from minibot.config.schema import Base


def _normalize_path(path: str) -> str:
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def _verify_gocqhttp_signature(body: bytes, secret: str, header_value: str | None) -> bool:
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


def _format_message_content(message: Any, raw_message: str = "") -> str:
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
            "at": "@",
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
            if seg_type in placeholders:
                parts.append(placeholders[seg_type])
        text = "".join(parts).strip()
        if text:
            return text

    return (raw_message or "").strip()


def _extract_inbound_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract normalized inbound fields from a OneBot event."""
    if str(payload.get("post_type") or "") != "message":
        return None

    message_type = str(payload.get("message_type") or "").strip().lower()
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        return None

    if message_type == "group":
        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            return None
        chat_id = f"group:{group_id}"
    else:
        message_type = "private"
        chat_id = f"private:{user_id}"

    content = _format_message_content(payload.get("message"), str(payload.get("raw_message") or ""))
    if not content:
        return None

    return {
        "sender_id": user_id,
        "chat_id": chat_id,
        "content": content,
        "metadata": {
            "qq_webhook": {
                "message_type": message_type,
                "message_id": payload.get("message_id"),
                "user_id": payload.get("user_id"),
                "group_id": payload.get("group_id"),
                "self_id": payload.get("self_id"),
            }
        },
    }


def _parse_target_chat(chat_id: str) -> tuple[str, str, str]:
    """Map chat_id to OneBot API endpoint and target field."""
    value = str(chat_id or "").strip()
    if value.startswith("private:"):
        target = value.split(":", 1)[1].strip()
        if target:
            return "send_private_msg", "user_id", target
    if value.startswith("group:"):
        target = value.split(":", 1)[1].strip()
        if target:
            return "send_group_msg", "group_id", target
    raise ValueError(
        f"qq_webhook chat_id must be 'private:<user_id>' or 'group:<group_id>', got: {chat_id}"
    )


class QQWebhookConfig(Base):
    """go-cqhttp reverse HTTP webhook channel configuration."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    path: str = "/qq/webhook"
    secret: str = ""
    api_base: str = "http://127.0.0.1:5700"
    access_token: str = ""
    allow_from: list[str] = Field(default_factory=lambda: ["*"])

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return _normalize_path(value)


class QQWebhookChannel(BaseChannel):
    """QQ channel using go-cqhttp reverse HTTP webhook."""

    name = "qq_webhook"
    display_name = "QQ (go-cqhttp Webhook)"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = QQWebhookConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: QQWebhookConfig = config
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._http: aiohttp.ClientSession | None = None

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return QQWebhookConfig().model_dump(by_alias=True)

    async def start(self) -> None:
        self._running = True
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

        app = web.Application()
        app.router.add_post(self.config.path, self._handle_webhook)
        app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await self._site.start()

        logger.info(
            "QQ webhook listening on http://{}:{}{}",
            self.config.host,
            self.config.port,
            self.config.path,
        )

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

    async def send(self, msg: OutboundMessage) -> None:
        if self._http is None:
            raise RuntimeError("qq_webhook HTTP client not initialized")

        endpoint, target_key, target_id = _parse_target_chat(msg.chat_id)
        url = f"{self.config.api_base.rstrip('/')}/{endpoint}"

        content = msg.content or ""
        if msg.media:
            media_lines = "\n".join(f"- {item}" for item in msg.media)
            suffix = f"\n\n[attachments]\n{media_lines}"
            content = f"{content}{suffix}" if content else f"[attachments]\n{media_lines}"
        if not content.strip():
            content = " "

        payload: dict[str, Any] = {
            target_key: target_id,
            "message": content,
        }
        if msg.reply_to:
            payload["auto_escape"] = False
            payload["message"] = f"[CQ:reply,id={msg.reply_to}]{content}"

        headers = {"Content-Type": "application/json"}
        token = self.config.access_token.strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = await self._http.post(url, json=payload, headers=headers)
        if resp.status >= 400:
            detail = (await resp.text())[:500]
            raise RuntimeError(
                f"qq_webhook send failed status={resp.status} endpoint={endpoint} detail={detail}"
            )

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.read()
        signature = request.headers.get("X-Signature") or request.headers.get("x-signature")
        if not _verify_gocqhttp_signature(body, self.config.secret.strip(), signature):
            logger.warning("qq_webhook: signature verification failed")
            return web.json_response({"error": "invalid signature"}, status=401)

        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        inbound = _extract_inbound_message(payload)
        if inbound is None:
            return web.json_response({"ok": True})

        try:
            await self._handle_message(
                sender_id=inbound["sender_id"],
                chat_id=inbound["chat_id"],
                content=inbound["content"],
                metadata=inbound["metadata"],
            )
        except Exception:
            logger.exception("qq_webhook: failed to handle inbound event")
            return web.json_response({"error": "internal error"}, status=500)

        return web.json_response({"ok": True})
