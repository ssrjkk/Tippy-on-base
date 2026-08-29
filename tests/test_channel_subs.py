"""Channel subscription kick lifecycle: mock-based unit tests."""

import time
from unittest.mock import AsyncMock

import pytest

from bot import channel_subs


@pytest.fixture()
def mock_bot():
    bot = AsyncMock()
    bot.ban_chat_member = AsyncMock()
    bot.unban_chat_member = AsyncMock()
    return bot


OWNER = 9999  # channel owner (separate from subscribers)


@pytest.mark.asyncio
async def test_active_sub_not_kicked(mock_bot, ledger, monkeypatch):
    tg_id = 5001
    chat_id = -100100
    ledger.ensure_user(OWNER, "owner")
    ledger.ensure_user(tg_id, "alice")
    ledger.credit(tg_id, 10_000_000, "test")
    ledger.set_paywall_channel(chat_id, OWNER, 100)
    ledger.subscribe_channel(chat_id, tg_id)
    monkeypatch.setattr(channel_subs, "ledger", ledger)

    kicked = await channel_subs.kick_expired_channel_subscriptions(mock_bot)
    assert kicked == 0
    mock_bot.ban_chat_member.assert_not_called()


@pytest.mark.asyncio
async def test_expired_sub_kicked(mock_bot, ledger, monkeypatch):
    tg_id = 5002
    chat_id = -100200
    ledger.ensure_user(OWNER, "owner")
    ledger.ensure_user(tg_id, "bob")
    ledger.credit(tg_id, 10_000_000, "test")
    ledger.set_paywall_channel(chat_id, OWNER, 100)
    ledger.subscribe_channel(chat_id, tg_id)
    ledger._conn.execute(
        "UPDATE paywall_subscriptions SET expires_at = %s "
        "WHERE chat_id = %s AND tg_id = %s",
        (int(time.time()) - 1, chat_id, tg_id),
    )
    ledger._conn.commit()
    monkeypatch.setattr(channel_subs, "ledger", ledger)

    kicked = await channel_subs.kick_expired_channel_subscriptions(mock_bot)
    assert kicked == 1
    mock_bot.ban_chat_member.assert_called_once()
    mock_bot.unban_chat_member.assert_called_once()


@pytest.mark.asyncio
async def test_ban_failure_handled(mock_bot, ledger, monkeypatch):
    tg_id = 5003
    chat_id = -100300
    ledger.ensure_user(OWNER, "owner")
    ledger.ensure_user(tg_id, "carol")
    ledger.credit(tg_id, 10_000_000, "test")
    ledger.set_paywall_channel(chat_id, OWNER, 100)
    ledger.subscribe_channel(chat_id, tg_id)
    ledger._conn.execute(
        "UPDATE paywall_subscriptions SET expires_at = %s "
        "WHERE chat_id = %s AND tg_id = %s",
        (int(time.time()) - 1, chat_id, tg_id),
    )
    ledger._conn.commit()
    monkeypatch.setattr(channel_subs, "ledger", ledger)

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import BanChatMember
    mock_bot.ban_chat_member.side_effect = TelegramBadRequest(method=BanChatMember(chat_id=chat_id, user_id=tg_id), message="bot is not administrator")

    kicked = await channel_subs.kick_expired_channel_subscriptions(mock_bot)
    assert kicked == 0
    mock_bot.ban_chat_member.assert_called_once()


@pytest.mark.asyncio
async def test_no_channels_configured(mock_bot, ledger, monkeypatch):
    monkeypatch.setattr(channel_subs, "ledger", ledger)

    kicked = await channel_subs.kick_expired_channel_subscriptions(mock_bot)
    assert kicked == 0
    mock_bot.ban_chat_member.assert_not_called()
