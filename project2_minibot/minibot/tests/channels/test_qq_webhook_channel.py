from __future__ import annotations

import json
import hashlib
import hmac
from pathlib import Path
from typing import Any

import pytest

from minibot.bus.events import OutboundMessage
from minibot.bus.queue import MessageBus
from minibot.channels.qq_webhook import (
    QQWebhookChannel,
    _build_onebot_message_value,
    _extract_inbound_message,
    _format_message_content,
    _normalize_path,
    _parse_target_chat,
    _verify_gocqhttp_signature,
)


def test_normalize_path_handles_slashes() -> None:
    assert _normalize_path("") == "/"
    assert _normalize_path("qq/webhook") == "/qq/webhook"
    assert _normalize_path("/qq/webhook/") == "/qq/webhook"


def test_verify_gocqhttp_signature_accepts_valid_and_rejects_invalid() -> None:
    body = b'{"post_type":"message"}'
    secret = "abc123"
    signed = "sha1=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    assert _verify_gocqhttp_signature(body, secret, signed)
    assert not _verify_gocqhttp_signature(body, secret, "sha1=bad")
    assert not _verify_gocqhttp_signature(body, secret, None)
    assert _verify_gocqhttp_signature(body, "", None)


def test_format_message_content_supports_segments() -> None:
    # at-segment without an explicit `name` falls back to the QQ number so the
    # rendered text still identifies the mentioned user (rather than a bare '@').
    message = [
        {"type": "text", "data": {"text": "hello"}},
        {"type": "at", "data": {"qq": "123"}},
        {"type": "image", "data": {"file": "x.png"}},
        {"type": "text", "data": {"text": " world"}},
    ]
    assert _format_message_content(message, "") == "hello@123[image] world"


def test_format_message_content_at_segment_prefers_name_and_handles_all() -> None:
    # An explicit display name wins over the raw QQ number.
    named = [{"type": "at", "data": {"qq": "42", "name": "HelloAgent"}}]
    assert _format_message_content(named, "") == "@HelloAgent"

    # qq=="all" is rendered with the conventional Chinese label.
    all_msg = [{"type": "at", "data": {"qq": "all"}}]
    assert _format_message_content(all_msg, "") == "@全体成员"


def test_extract_inbound_message_private_and_group() -> None:
    private_payload = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 10001,
        "message_id": 1,
        "message": "hi",
    }
    private_msg = _extract_inbound_message(private_payload)
    assert private_msg is not None
    assert private_msg["sender_id"] == "10001"
    assert private_msg["chat_id"] == "private:10001"
    assert private_msg["content"] == "hi"

    group_payload = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 20001,
        "user_id": 10001,
        "message_id": 2,
        "message": [{"type": "text", "data": {"text": "hello group"}}],
    }
    group_msg = _extract_inbound_message(group_payload)
    assert group_msg is not None
    assert group_msg["chat_id"] == "group:20001"
    assert group_msg["content"] == "hello group"


def test_extract_inbound_message_ignores_non_message_events() -> None:
    payload = {"post_type": "notice", "notice_type": "group_recall"}
    assert _extract_inbound_message(payload) is None


def test_build_onebot_message_value_file_segment_for_pptx(tmp_path) -> None:
    ppt = tmp_path / "slide.pptx"
    ppt.write_bytes(b"hello")
    msg, escape = _build_onebot_message_value(
        content="please see",
        media=[str(ppt)],
        reply_id=None,
        use_segments=True,
    )
    assert escape is True
    assert isinstance(msg, list)
    assert msg[0] == {"type": "text", "data": {"text": "please see"}}
    assert msg[1]["type"] == "file"
    assert msg[1]["data"]["name"] == "slide.pptx"
    assert Path(msg[1]["data"]["file"]) == ppt.resolve()


def test_parse_target_chat() -> None:
    assert _parse_target_chat("private:1001") == ("send_private_msg", "user_id", "1001")
    assert _parse_target_chat("group:2002") == ("send_group_msg", "group_id", "2002")
    with pytest.raises(ValueError):
        _parse_target_chat("2002")


class _FakeResponse:
    def __init__(self, status: int = 200, text: str = ""):
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text


class _FakeHttpSession:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.next_response: _FakeResponse = _FakeResponse()

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.next_response

    async def close(self) -> None:
        return None


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body


@pytest.mark.asyncio
async def test_send_private_message_calls_onebot_api() -> None:
    channel = QQWebhookChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "apiBase": "http://127.0.0.1:5700",
            "accessToken": "tok",
        },
        MessageBus(),
    )
    fake_http = _FakeHttpSession()
    channel._http = fake_http  # type: ignore[assignment]

    await channel.send(
        OutboundMessage(
            channel="qq_webhook",
            chat_id="private:1001",
            content="hello",
        )
    )

    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["url"] == "http://127.0.0.1:5700/send_private_msg"
    assert call["json"] == {"user_id": "1001", "message": "hello"}
    assert call["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_send_group_message_with_reply_and_media(tmp_path) -> None:
    channel = QQWebhookChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "apiBase": "http://127.0.0.1:5700",
        },
        MessageBus(),
    )
    fake_http = _FakeHttpSession()
    channel._http = fake_http  # type: ignore[assignment]

    png = tmp_path / "a.png"
    txt = tmp_path / "b.txt"
    png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")
    txt.write_text("x", encoding="utf-8")

    await channel.send(
        OutboundMessage(
            channel="qq_webhook",
            chat_id="group:2002",
            content="result",
            reply_to="123",
            media=[str(png), str(txt)],
        )
    )

    assert len(fake_http.calls) == 1
    payload = fake_http.calls[0]["json"]
    assert payload["group_id"] == "2002"
    assert payload["auto_escape"] is False
    msg = payload["message"]
    assert isinstance(msg, list)
    assert msg[0]["type"] == "text"
    assert msg[0]["data"]["text"].startswith("[CQ:reply,id=123]")
    assert "result" in msg[0]["data"]["text"]
    assert msg[1]["type"] == "image"
    assert Path(msg[1]["data"]["file"]) == png.resolve()
    assert msg[2]["type"] == "file"
    assert msg[2]["data"]["name"] == "b.txt"
    assert Path(msg[2]["data"]["file"]) == txt.resolve()


@pytest.mark.asyncio
async def test_send_group_message_legacy_text_paths_when_segments_disabled() -> None:
    channel = QQWebhookChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "apiBase": "http://127.0.0.1:5700",
            "sendMediaAsOnebotSegments": False,
        },
        MessageBus(),
    )
    fake_http = _FakeHttpSession()
    channel._http = fake_http  # type: ignore[assignment]

    await channel.send(
        OutboundMessage(
            channel="qq_webhook",
            chat_id="group:2002",
            content="result",
            reply_to="123",
            media=["/tmp/a.png", "/tmp/b.txt"],
        )
    )

    payload = fake_http.calls[0]["json"]
    assert isinstance(payload["message"], str)
    assert payload["message"].startswith("[CQ:reply,id=123]")
    assert "[attachments]" in payload["message"]


@pytest.mark.asyncio
async def test_send_raises_when_onebot_returns_nonzero_retcode() -> None:
    channel = QQWebhookChannel(
        {"enabled": True, "allowFrom": ["*"]},
        MessageBus(),
    )
    fake_http = _FakeHttpSession()
    fake_http.next_response = _FakeResponse(
        status=200, text='{"status":"failed","retcode":1404,"wording":"bad"}'
    )
    channel._http = fake_http  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="retcode"):
        await channel.send(
            OutboundMessage(
                channel="qq_webhook",
                chat_id="private:1001",
                content="hello",
            )
        )


@pytest.mark.asyncio
async def test_send_uses_message_id_from_metadata_when_reply_to_absent() -> None:
    channel = QQWebhookChannel(
        {"enabled": True, "allowFrom": ["*"], "apiBase": "http://127.0.0.1:5700"},
        MessageBus(),
    )
    fake_http = _FakeHttpSession()
    fake_http.next_response = _FakeResponse(status=200, text='{"status":"ok","retcode":0}')
    channel._http = fake_http  # type: ignore[assignment]

    await channel.send(
        OutboundMessage(
            channel="qq_webhook",
            chat_id="group:2002",
            content="hi",
            metadata={"message_id": 999},
        )
    )
    payload = fake_http.calls[0]["json"]
    assert payload["message"].startswith("[CQ:reply,id=999]")


@pytest.mark.asyncio
async def test_send_raises_when_onebot_api_returns_error() -> None:
    channel = QQWebhookChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
        },
        MessageBus(),
    )
    fake_http = _FakeHttpSession()
    fake_http.next_response = _FakeResponse(status=500, text="boom")
    channel._http = fake_http  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="qq_webhook send failed"):
        await channel.send(
            OutboundMessage(
                channel="qq_webhook",
                chat_id="private:1001",
                content="hello",
            )
        )


@pytest.mark.asyncio
async def test_handle_webhook_forwards_valid_message() -> None:
    bus = MessageBus()
    channel = QQWebhookChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "secret": "s1",
        },
        bus,
    )

    body = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 1001,
        "message_id": 7,
        "message": "hello",
    }
    raw = json.dumps(body).encode("utf-8")
    sig_ok = "sha1=" + hmac.new(b"s1", raw, hashlib.sha1).hexdigest()
    req = _FakeRequest(raw, {"X-Signature": sig_ok})
    resp = await channel._handle_webhook(req)  # type: ignore[arg-type]
    assert resp.status == 200

    inbound = await bus.consume_inbound()
    assert inbound.channel == "qq_webhook"
    assert inbound.sender_id == "1001"
    assert inbound.chat_id == "private:1001"
    assert inbound.content == "hello"
    assert inbound.metadata.get("message_id") == 7
    assert inbound.metadata.get("qq_webhook", {}).get("message_id") == 7


@pytest.mark.asyncio
async def test_handle_webhook_rejects_bad_signature() -> None:
    channel = QQWebhookChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "secret": "s1",
        },
        MessageBus(),
    )

    payload = json.dumps({"post_type": "message"}).encode("utf-8")
    req = _FakeRequest(payload, {"X-Signature": "sha1=bad"})
    resp = await channel._handle_webhook(req)  # type: ignore[arg-type]
    assert resp.status == 401
