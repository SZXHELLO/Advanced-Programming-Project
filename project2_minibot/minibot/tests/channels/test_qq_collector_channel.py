from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from minibot.bus.events import OutboundMessage
from minibot.bus.queue import MessageBus
from minibot.channels.qq_collector import QQCollectorChannel, _extract_mention_kind


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body


def _group_payload(message: list[dict], *, self_id: int = 42) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": 123,
        "self_id": self_id,
        "user_id": 10001,
        "message_id": 9,
        "time": 1_714_000_000,
        "message": message,
        "sender": {"nickname": "Alice"},
    }


def test_extract_mention_kind_all_me_none() -> None:
    payload_all = _group_payload(
        [{"type": "at", "data": {"qq": "all"}}, {"type": "text", "data": {"text": "hi"}}]
    )
    assert _extract_mention_kind(payload_all, match_at_me=True, match_at_all=True) == "all"

    payload_me = _group_payload(
        [{"type": "at", "data": {"qq": "42"}}, {"type": "text", "data": {"text": "hello"}}]
    )
    assert _extract_mention_kind(payload_me, match_at_me=True, match_at_all=True) == "me"

    payload_none = _group_payload([{"type": "text", "data": {"text": "normal message"}}])
    assert _extract_mention_kind(payload_none, match_at_me=True, match_at_all=True) is None


def test_extract_mention_kind_text_fallback_me_by_alias() -> None:
    """Literal '@<alias>' in plain text is treated as @me."""
    payload = _group_payload(
        [{"type": "text", "data": {"text": "@HelloAgent hello"}}], self_id=3223502353
    )
    payload["raw_message"] = "@HelloAgent hello"
    assert _extract_mention_kind(
        payload,
        match_at_me=True,
        match_at_all=True,
        text_mention_aliases=["HelloAgent"],
    ) == "me"


def test_extract_mention_kind_text_fallback_me_by_self_id() -> None:
    """Typing '@<bot_qq>' literally is also treated as @me without config."""
    payload = _group_payload(
        [{"type": "text", "data": {"text": "@42 ping"}}], self_id=42
    )
    payload["raw_message"] = "@42 ping"
    assert _extract_mention_kind(payload, match_at_me=True, match_at_all=True) == "me"


def test_extract_mention_kind_text_fallback_at_all_chinese() -> None:
    """Literal '@全体成员' in plain text is treated as @all."""
    payload = _group_payload([{"type": "text", "data": {"text": "@全体成员 please check"}}])
    payload["raw_message"] = "@全体成员 please check"
    assert _extract_mention_kind(payload, match_at_me=True, match_at_all=True) == "all"


def test_extract_mention_kind_text_fallback_at_all_word_boundary() -> None:
    """'@allergy' must not be misread as @all."""
    payload = _group_payload([{"type": "text", "data": {"text": "@allergy warning"}}])
    payload["raw_message"] = "@allergy warning"
    assert _extract_mention_kind(payload, match_at_me=True, match_at_all=True) is None


def test_extract_mention_kind_alias_does_not_leak_across_bots() -> None:
    """An alias only makes the current bot @me-mentioned, not some other ID."""
    payload = _group_payload(
        [{"type": "text", "data": {"text": "@Bob hi"}}], self_id=42
    )
    payload["raw_message"] = "@Bob hi"
    assert _extract_mention_kind(
        payload,
        match_at_me=True,
        match_at_all=True,
        text_mention_aliases=["HelloAgent"],
    ) is None


@pytest.mark.asyncio
async def test_handle_event_writes_markdown_when_mentioned(tmp_path) -> None:
    output_dir = tmp_path / "QQInfo"
    channel = QQCollectorChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "outputDir": str(output_dir),
        },
        MessageBus(),
    )
    payload = _group_payload(
        [
            {"type": "at", "data": {"qq": "all"}},
            {"type": "text", "data": {"text": " build status?"}},
        ]
    )

    await channel._handle_event(payload)

    file_path = output_dir / "group_123.md"
    assert file_path.exists()
    text = file_path.read_text(encoding="utf-8")
    assert "# QQ 群 123" in text
    assert "Alice (10001) [@all | msg_id=9]" in text
    assert "build status?" in text


@pytest.mark.asyncio
async def test_handle_event_ignores_plain_group_message_by_default(tmp_path) -> None:
    output_dir = tmp_path / "QQInfo"
    channel = QQCollectorChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "outputDir": str(output_dir),
        },
        MessageBus(),
    )
    payload = _group_payload([{"type": "text", "data": {"text": "no mention"}}])

    await channel._handle_event(payload)

    assert not (output_dir / "group_123.md").exists()


@pytest.mark.asyncio
async def test_handle_webhook_signature_validation(tmp_path) -> None:
    output_dir = tmp_path / "QQInfo"
    channel = QQCollectorChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "outputDir": str(output_dir),
            "secret": "s1",
        },
        MessageBus(),
    )
    raw = json.dumps(_group_payload([{"type": "at", "data": {"qq": "all"}}])).encode("utf-8")

    bad_req = _FakeRequest(raw, {"X-Signature": "sha1=bad"})
    bad_resp = await channel._handle_webhook(bad_req)  # type: ignore[arg-type]
    assert bad_resp.status == 401

    sig = "sha1=" + hmac.new(b"s1", raw, hashlib.sha1).hexdigest()
    good_req = _FakeRequest(raw, {"X-Signature": sig})
    good_resp = await channel._handle_webhook(good_req)  # type: ignore[arg-type]
    assert good_resp.status == 200
    assert (output_dir / "group_123.md").exists()


@pytest.mark.asyncio
async def test_send_is_noop() -> None:
    channel = QQCollectorChannel({"enabled": True, "allowFrom": ["*"]}, MessageBus())
    await channel.send(OutboundMessage(channel="qq_collector", chat_id="group:1", content="ignored"))


@pytest.mark.asyncio
async def test_resolve_me_aliases_merges_auto_and_config(tmp_path) -> None:
    """Auto-detected nickname/card from NapCat API must merge with user aliases."""
    channel = QQCollectorChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "outputDir": str(tmp_path),
            "textMentionAliases": ["Custom"],
        },
        MessageBus(),
    )
    # Inject fetched identities (normally populated by OneBot API calls at startup).
    channel._bot_nickname = "HelloAgent"
    channel._group_member_cards[("123", "42")] = "HelloAgentCard"

    aliases = await channel._resolve_me_aliases("123", "42")
    assert aliases == ["HelloAgent", "HelloAgentCard", "Custom"]


@pytest.mark.asyncio
async def test_handle_event_renders_at_segment_with_name(tmp_path) -> None:
    """Real @ popup must render as '@<name>' in the markdown, not bare '@'."""
    output_dir = tmp_path / "QQInfo"
    channel = QQCollectorChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "outputDir": str(output_dir),
        },
        MessageBus(),
    )
    channel._bot_nickname = "HelloAgent"
    # Pre-populate the member cache so _enrich_at_names doesn't try the network.
    channel._group_member_cards[("123", "42")] = "HelloAgent"

    payload = _group_payload(
        [
            {"type": "at", "data": {"qq": "42"}},
            {"type": "text", "data": {"text": " hello"}},
        ]
    )
    await channel._handle_event(payload)

    md = (output_dir / "group_123.md").read_text(encoding="utf-8")
    assert "@HelloAgent hello" in md
    assert "[@me " in md


@pytest.mark.asyncio
async def test_handle_event_renders_at_all_with_chinese_label(tmp_path) -> None:
    """'qq=all' at-segment is rendered as '@全体成员'."""
    output_dir = tmp_path / "QQInfo"
    channel = QQCollectorChannel(
        {"enabled": True, "allowFrom": ["*"], "outputDir": str(output_dir)},
        MessageBus(),
    )
    payload = _group_payload(
        [
            {"type": "at", "data": {"qq": "all"}},
            {"type": "text", "data": {"text": " please check"}},
        ]
    )
    await channel._handle_event(payload)
    md = (output_dir / "group_123.md").read_text(encoding="utf-8")
    assert "@全体成员 please check" in md


@pytest.mark.asyncio
async def test_handle_event_preserves_explicit_name_on_at_segment(tmp_path) -> None:
    """If NapCat already filled in data.name, we must not overwrite it."""
    output_dir = tmp_path / "QQInfo"
    channel = QQCollectorChannel(
        {"enabled": True, "allowFrom": ["*"], "outputDir": str(output_dir)},
        MessageBus(),
    )
    channel._bot_nickname = "SomethingElse"
    channel._group_member_cards[("123", "42")] = "WouldBeOverride"
    payload = _group_payload(
        [
            {"type": "at", "data": {"qq": "42", "name": "ChosenDisplay"}},
            {"type": "text", "data": {"text": " hi"}},
        ]
    )
    await channel._handle_event(payload)
    md = (output_dir / "group_123.md").read_text(encoding="utf-8")
    assert "@ChosenDisplay hi" in md


@pytest.mark.asyncio
async def test_handle_event_uses_auto_detected_nickname(tmp_path) -> None:
    """A literal '@<nickname>' from NapCat-fetched nickname triggers @me even
    when the user left `textMentionAliases` empty."""
    output_dir = tmp_path / "QQInfo"
    channel = QQCollectorChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "outputDir": str(output_dir),
        },
        MessageBus(),
    )
    # Simulate a successful `get_login_info` without going over the network.
    channel._bot_nickname = "HelloAgent"
    channel.config.auto_fetch_bot_name = False  # skip the live refresh attempt

    payload = _group_payload(
        [{"type": "text", "data": {"text": "@HelloAgent hi"}}], self_id=3223502353
    )
    payload["raw_message"] = "@HelloAgent hi"

    # _resolve_me_aliases still needs the nickname even with auto_fetch disabled
    # above — re-enable it so the cached nickname is included in alias list.
    channel.config.auto_fetch_bot_name = True

    # Bypass the per-group card API call by pre-caching an empty result.
    channel._group_member_cards[("123", "3223502353")] = ""

    await channel._handle_event(payload)

    file_path = output_dir / "group_123.md"
    assert file_path.exists()
    assert "@me" in file_path.read_text(encoding="utf-8")
