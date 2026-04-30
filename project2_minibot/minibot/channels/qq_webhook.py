"""QQ channel for go-cqhttp reverse HTTP webhook (OneBot v11)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

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
from minibot.config.schema import QQWebhookConfig, _normalize_webhook_path


def _normalize_path(path: str) -> str:
    """Backward-compatible wrapper kept for tests and external callers."""
    return _normalize_webhook_path(path)


def _verify_gocqhttp_signature(body: bytes, secret: str, header_value: str | None) -> bool:
    """Backward-compatible wrapper around shared OneBot signature verification."""
    return verify_gocqhttp_signature(body, secret, header_value)


def _format_message_content(message: Any, raw_message: str = "") -> str:
    """Backward-compatible wrapper around shared OneBot message formatter."""
    return format_onebot_message_content(message, raw_message)


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

    mid = payload.get("message_id")
    metadata: dict[str, Any] = {
        "qq_webhook": {
            "message_type": message_type,
            "message_id": mid,
            "user_id": payload.get("user_id"),
            "group_id": payload.get("group_id"),
            "self_id": payload.get("self_id"),
        }
    }
    # AgentLoop / MessageTool read ``message_id`` at the top level of metadata
    # for tool context and reply threading — keep it in sync with qq_webhook.
    if mid is not None:
        metadata["message_id"] = mid

    return {
        "sender_id": user_id,
        "chat_id": chat_id,
        "content": content,
        "metadata": metadata,
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
        reply_id = msg.reply_to
        if not reply_id and msg.metadata:
            mid = msg.metadata.get("message_id")
            if mid is not None:
                reply_id = str(mid).strip()
        if reply_id:
            payload["auto_escape"] = False
            payload["message"] = f"[CQ:reply,id={reply_id}]{content}"

        headers = {"Content-Type": "application/json"}
        token = self.config.access_token.strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = await self._http.post(url, json=payload, headers=headers)
        raw_text = (await resp.text())[:2000]
        if resp.status >= 400:
            raise RuntimeError(
                f"qq_webhook send failed status={resp.status} endpoint={endpoint} detail={raw_text}"
            )
        try:
            data = json.loads(raw_text) if raw_text.strip() else None
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            rc = data.get("retcode")
            st = str(data.get("status") or "").lower()
            if rc is not None:
                try:
                    rc_int = int(rc)
                except (TypeError, ValueError):
                    rc_int = None
                if rc_int is not None and rc_int != 0:
                    raise RuntimeError(
                        f"qq_webhook OneBot API retcode={rc} status={st!r} "
                        f"endpoint={endpoint} detail={raw_text[:500]}"
                    )
            if st and st not in ("ok", "async"):
                raise RuntimeError(
                    f"qq_webhook OneBot API status={st!r} endpoint={endpoint} detail={raw_text[:500]}"
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
