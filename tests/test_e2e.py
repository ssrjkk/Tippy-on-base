"""E2E scenarios: full user journeys through the real production code.

Each scenario drives the actual handlers, ledger, base (RPC) layer, main.py
watcher tasks and the FastAPI dashboard end to end. Only the external network
is mocked (Telegram transport, Base RPC); real crypto (signing, ABI decoding),
SQLite, fee math and conservation invariants are exercised for real.
"""

import asyncio
import threading
import time
import types
from decimal import Decimal

import pytest
from aiogram.filters import CommandObject
from aiogram.types import ReactionTypeEmoji
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from hexbytes import HexBytes
from web3 import Web3

from bot import base, config
from bot import main as botmain
from bot.handlers import (
    cb_bet_amount,
    cb_bet_place,
    cmd_balance,
    cmd_bet,
    cmd_bets,
    cmd_cancel,
    cmd_claim,
    cmd_confirm,
    cmd_deposit,
    cmd_history,
    cmd_link,
    cmd_mybets,
    cmd_resolve,
    cmd_start,
    cmd_stats,
    cmd_tip,
    cmd_withdraw,
    on_reaction,
)
from web import server as web_server
from web.auth import COOKIE_NAME, make_session

ALICE, BOB, CAROL = 2001, 2002, 2003
USDC = 10**6  # micro-units per USDC

ACC = Account.from_key("0x" + "22" * 32)
ACC2 = Account.from_key("0x" + "33" * 32)

# Real ABI decoder for Transfer events (pure local, no network). The fake RPC
# below keeps the real contract's decoder so log decoding is exercised for real.
TRANSFER_DECODER = base.usdc.events.Transfer()


# ---------- mocks (same shape as test_handlers) ----------


class User:
    def __init__(self, id, username=None, is_bot=False):
        self.id = id
        self.username = username
        self.is_bot = is_bot


class Chat:
    def __init__(self, id=-1000, members=()):
        self.id = id
        self.members = list(members)

    async def get_members(self, limit=200):
        for m in self.members[:limit]:
            yield m


class Bot:
    def __init__(self):
        self.sent = []
        self.evt = asyncio.Event()
        self.username = "base_tipbot"

    async def send_message(self, chat_id, text=None, **kw):
        self.sent.append((chat_id, text))
        self.evt.set()

    async def get_me(self):
        return types.SimpleNamespace(username=self.username)


class Message:
    def __init__(self, text="", from_id=ALICE, username="alice", bot=None, chat=None, reply_to=None, message_id=1):
        self.text = text
        self.from_user = User(from_id, username)
        self.bot = bot or Bot()
        self.chat = chat or Chat()
        self.reply_to_message = reply_to
        self.message_id = message_id
        self.answers = []
        self.photos = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))

    async def answer_photo(self, photo, caption=None, reply_markup=None, **kw):
        self.photos.append((photo, caption, reply_markup))


class AnswerRecorder:
    def __init__(self, bot=None):
        self.text = None
        self.markup = None
        self.caption = None
        self.bot = bot or Bot()

    async def edit_text(self, text, reply_markup=None, **kw):
        self.text = text
        self.markup = reply_markup

    async def edit_caption(self, caption=None, reply_markup=None, **kw):
        self.caption = caption
        self.markup = reply_markup


class Callback:
    def __init__(self, data, from_id, bot=None):
        self.data = data
        self.from_user = User(from_id)
        self.message = AnswerRecorder(bot)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def run(coro):
    return asyncio.run(coro)


# ---------- RPC mock (real ABI decoding, fake network) ----------


def _to_wei(value, unit):
    units = {"wei": 1, "gwei": 10**9, "ether": 10**18}
    return int(Decimal(str(value)) * units[unit])


def _transfer_log(sender, receiver, value_micro, tx_hash, block=1000):
    """Build a real USDC Transfer log so the actual contract ABI decodes it."""

    def pad(a):
        return "0x" + "0" * 24 + a[2:].lower()

    return {
        "address": base.USDC,
        "topics": [
            Web3.keccak(text="Transfer(address,address,uint256)").hex(),
            pad(sender),
            pad(receiver),
        ],
        "data": "0x" + f"{int(value_micro):064x}",
        "transactionHash": HexBytes(tx_hash if isinstance(tx_hash, bytes) else tx_hash[2:]),
        "blockNumber": block,
        "logIndex": 0,
        "transactionIndex": 0,
        "blockHash": HexBytes(b"\x00" * 32),
    }


def install_rpc(monkeypatch, logs=(), block=1000, receipts=None, fail_first=0, tx_count=7):
    """Fake Base RPC. get_logs fails the first `fail_first` calls (RPC outage)."""
    state = {"calls": 0, "sent_raw": []}
    captured = {}

    def get_logs(q):
        state["calls"] += 1
        if state["calls"] <= fail_first:
            raise ConnectionError("rpc down")
        return list(logs)

    class FakeTransfer:
        def build_transaction(self, kwargs):
            return {**kwargs, "data": b"x", "gas": 60000, "chainId": 8453}

    class FakeFunctions:
        def transfer(self, to, amount):
            captured["to"], captured["amount"] = to, amount
            return FakeTransfer()

    def send_raw(raw):
        state["sent_raw"].append(raw)
        return b"\x01" * 32

    eth = types.SimpleNamespace(
        account=base.w3.eth.account,
        block_number=block,
        chain_id=8453,
        get_logs=get_logs,
        get_transaction_receipt=lambda h: (receipts or {}).get(h),
        get_transaction_count=lambda a, p: tx_count,
        get_block=lambda x: {"baseFeePerGas": 1_000_000_000},
        send_raw_transaction=send_raw,
    )
    monkeypatch.setattr(base, "w3", types.SimpleNamespace(eth=eth, to_wei=_to_wei))
    monkeypatch.setattr(
        base,
        "usdc",
        types.SimpleNamespace(
            functions=FakeFunctions(),
            events=types.SimpleNamespace(Transfer=lambda: TRANSFER_DECODER),
        ),
    )
    return state, captured


# ---------- fixtures ----------


@pytest.fixture()
def e2e(ledger, monkeypatch):
    """Fresh ledger wired into handlers, the base layer AND the bot watchers;
    no throttling."""
    from bot.ledger import AsyncLedger
    monkeypatch.setattr(config, "MONEY_CMD_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(base, "ledger", ledger)
    monkeypatch.setattr(botmain, "ledger", AsyncLedger(ledger))
    return ledger


@pytest.fixture()
def api(e2e, monkeypatch):
    from bot.ledger import AsyncLedger
    monkeypatch.setattr(web_server, "ledger", AsyncLedger(e2e))
    async def _hb():
        return 500.0
    monkeypatch.setattr(web_server, "hot_balance", _hb)
    return TestClient(web_server.app)


def fund(ledger, tg_id, usdc):
    ledger.credit(tg_id, int(usdc * USDC), "deposit")


def conservation(ledger):
    """Return what must equal total deposits: balances + sent withdrawals + fees.
    Refunded withdrawals are excluded (the money was debited AND returned)."""
    liabilities = ledger.total_liabilities()
    rows = ledger._conn.execute(
        "SELECT kind, SUM(amount) AS s FROM tx_log "
        "WHERE kind IN ('withdraw', 'fee') AND COALESCE(status, '') != 'refunded' "
        "GROUP BY kind"
    ).fetchall()
    by_kind = {r["kind"]: int(r["s"]) for r in rows}
    return liabilities + by_kind.get("withdraw", 0) + by_kind.get("fee", 0)


def start(bot, tg_id, username, args=None):
    m = Message("/start", from_id=tg_id, username=username, bot=bot)
    run(cmd_start(m, CommandObject(command="start", args=args)))
    return m


# ---------- scenario 1: full user journey ----------


def test_e2e_user_journey_deposit_tip_withdraw(e2e, monkeypatch):
    bot = Bot()
    state, captured = install_rpc(monkeypatch, block=1500)

    # /start registers the user
    m = start(bot, ALICE, "alice")
    assert "Привет" in m.answers[0][0]
    assert e2e.user_exists(ALICE)
    assert e2e.balance(ALICE) == 0

    # /deposit shows the hot wallet address as QR caption
    m = Message("/deposit", from_id=ALICE, bot=bot)
    run(cmd_deposit(m))
    assert str(base.hot_wallet()) in m.photos[0][1]

    # link a wallet with a real signature
    m = Message(f"/link {ACC.address}", from_id=ALICE, bot=bot)
    run(cmd_link(m))
    row = e2e.get_link_nonce(ALICE)
    sign_text = f"Tippy: link {ALICE}:{row['nonce']}"
    sig = ACC.sign_message(encode_defunct(text=sign_text)).signature.hex()
    m = Message(f"/confirm 0x{sig}", from_id=ALICE, bot=bot)
    run(cmd_confirm(m))
    assert e2e.linked_address(ALICE).lower() == ACC.address.lower()
    assert "привязан" in m.answers[0][0]

    # a real USDC transfer arrives; the sweep credits it and the watcher DMs
    tx1 = "0x" + "aa" * 32
    logs1 = [_transfer_log(ACC.address, str(base.hot_wallet()), 100 * USDC, tx1)]
    _, captured = install_rpc(monkeypatch, logs=logs1, block=1500)
    credited = asyncio.run(base.poll_deposits())
    assert credited == [{"tg_id": ALICE, "amount_micro": 100 * USDC, "tx_hash": tx1}]
    assert e2e.balance(ALICE) == Decimal("100.000000")

    # a second deposit arrives; the deposit_watcher task must DM the user
    tx2 = "0x" + "ab" * 32
    logs2 = [_transfer_log(ACC.address, str(base.hot_wallet()), 10 * USDC, tx2)]
    _, captured2 = install_rpc(monkeypatch, logs=logs2, block=1510)
    monkeypatch.setattr(botmain, "bot", bot)
    monkeypatch.setattr(config, "POLL_SECONDS", 0.01)

    async def _run_deposit_watcher():
        t = asyncio.create_task(botmain.deposit_watcher())
        await bot.evt.wait()
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    asyncio.run(_run_deposit_watcher())
    assert any("Депозит зачислен" in text and "10 USDC" in text for _, text in bot.sent)
    assert e2e.balance(ALICE) == Decimal("110.000000")

    # deposit notifications muted in /settings: watcher stays silent
    tx3 = "0x" + "ac" * 32
    logs3 = [_transfer_log(ACC.address, str(base.hot_wallet()), 5 * USDC, tx3)]
    _, captured3 = install_rpc(monkeypatch, logs=logs3, block=1520)
    asyncio.run(botmain.ledger.set_setting(ALICE, "notify_deposits", False))
    bot.sent.clear()

    async def _run_deposit_watcher_muted():
        t = asyncio.create_task(botmain.deposit_watcher())
        await asyncio.sleep(0.1)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    asyncio.run(_run_deposit_watcher_muted())
    assert not any("Депозит зачислен" in text for _, text in bot.sent)
    assert e2e.balance(ALICE) == Decimal("115.000000")
    asyncio.run(botmain.ledger.set_setting(ALICE, "notify_deposits", True))

    # /balance shows it
    m = Message("/balance", from_id=ALICE, bot=bot)
    run(cmd_balance(m))
    assert "115" in m.answers[0][0]

    # register Bob and tip him 5 USDC
    start(bot, BOB, "bob")
    m = Message("/tip 5 @bob", from_id=ALICE, username="alice", bot=bot)
    run(cmd_tip(m))
    assert "5" in m.answers[0][0]
    assert e2e.balance(ALICE) == Decimal("110.000000")
    assert e2e.balance(BOB) == Decimal("5.000000")
    assert any(cid == BOB and "Тебе кинули" in text for cid, text in bot.sent)

    # history / stats reflect everything
    m = Message("/history", from_id=ALICE, bot=bot)
    run(cmd_history(m))
    h = m.answers[0][0]
    assert "+100" in h and "+10" in h and "+5" in h and "5 → @bob" in h
    m = Message("/stats", from_id=ALICE, bot=bot)
    run(cmd_stats(m))
    assert "Отправил" in m.answers[0][0]

    # withdraw 10 USDC (1% fee = 0.1): balance 110 - 10 - 0.1 = 99.9
    to_addr = ACC2.address
    m = Message(f"/withdraw {to_addr} 10", from_id=ALICE, bot=bot)
    run(cmd_withdraw(m))
    assert "Отправлено" in m.answers[0][0]
    assert "basescan.org/tx/" in m.answers[0][0]
    assert captured3["to"] == to_addr and captured3["amount"] == 10 * USDC
    assert e2e.balance(ALICE) == Decimal("99.900000")
    assert e2e.withdrawals_today(ALICE) == 1
    # fee was recorded and the withdraw row is 'done'
    fees = e2e._conn.execute(
        "SELECT amount FROM tx_log WHERE kind='fee' AND tg_id=%s ORDER BY id DESC LIMIT 1",
        (ALICE,),
    ).fetchone()
    assert fees["amount"] == 100_000
    wd = e2e._conn.execute(
        "SELECT status, tx_hash FROM tx_log WHERE kind='withdraw' AND tg_id=%s", (ALICE,)
    ).fetchone()
    assert wd["status"] == "done" and wd["tx_hash"].startswith("0x01")
    # nothing left pending for the withdraw watcher
    asyncio.run(base.check_pending_withdraws())
    assert e2e.pending_withdraws() == []

    # conservation: balances + sent withdrawals + fees == deposits
    assert conservation(e2e) == 115 * USDC
    # history shows the fee row as withdrawal fee
    m = Message("/history 50", from_id=ALICE, bot=bot)
    run(cmd_history(m))
    assert "комиссия вывода" in m.answers[0][0]

    # daily withdraw limit kicks in after MAX_WITHDRAWS_PER_DAY
    for _ in range(4):
        m = Message(f"/withdraw {to_addr} 1", from_id=ALICE, bot=bot)
        run(cmd_withdraw(m))
    m = Message(f"/withdraw {to_addr} 1", from_id=ALICE, bot=bot)
    run(cmd_withdraw(m))
    assert "Лимит" in m.answers[0][0]
    assert conservation(e2e) == 115 * USDC

    # min withdraw enforced
    m = Message(f"/withdraw {to_addr} 0.5", from_id=BOB, bot=bot)
    run(cmd_withdraw(m))
    assert "Минимум" in m.answers[0][0]


# ---------- scenario 2: market lifecycle ----------


def test_e2e_market_lifecycle_resolve_payouts(e2e, monkeypatch):
    install_rpc(monkeypatch, block=1500)
    bot = Bot()
    for tg, name in ((ALICE, "alice"), (BOB, "bob"), (CAROL, "carol")):
        start(bot, tg, name)
        fund(e2e, tg, 1000)
    assert e2e.total_liabilities() == 3000 * USDC

    # create a market with a 24h deadline
    m = Message("/bet create Кто победит? | Да | Нет 24h", from_id=ALICE, bot=bot)
    run(cmd_bet(m))
    assert "Ставка #1 создана" in m.answers[0][0]
    bet_id = 1
    bet = e2e.get_bet(bet_id)
    assert bet["close_at"] and bet["close_at"] - int(time.time()) < 24 * 3600 + 60

    # two backers bet through the real handler
    m = Message("/bet 1 1 100", from_id=BOB, bot=bot)
    run(cmd_bet(m))
    assert "Ставка принята" in m.answers[0][0]
    m = Message("/bet 1 1 50", from_id=BOB, bot=bot)
    run(cmd_bet(m))
    m = Message("/bet 1 2 100", from_id=CAROL, bot=bot)
    run(cmd_bet(m))
    assert e2e.balance(BOB) == Decimal("850.000000")
    assert e2e.balance(CAROL) == Decimal("900.000000")

    # /bets lists it; /mybets shows positions with potential payout
    m = Message("/bets", from_id=BOB, bot=bot)
    run(cmd_bets(m))
    assert "Кто победит?" in m.answers[0][0]
    m = Message("/mybets", from_id=BOB, bot=bot)
    run(cmd_mybets(m))
    assert "250" in m.answers[0][0]  # potential payout of the whole pot

    # resolve: parimutuel payouts are exact
    m = Message("/resolve 1 1", from_id=ALICE, bot=bot)
    run(cmd_resolve(m))
    assert "закрыта" in m.answers[0][0]
    assert e2e.balance(BOB) == Decimal("1098.000000")   # 850 + (250 - 2 fee)
    assert e2e.balance(CAROL) == Decimal("900.000000")  # lost stake
    assert e2e.balance(ALICE) == Decimal("1002.000000")  # 1000 + 2 creator fee
    assert e2e.creator_fees(ALICE) == 2 * USDC
    view = e2e.market_view(bet_id)
    assert view["status"] == "resolved" and view["winner"] == 0
    # conservation: markets never leave the system
    assert e2e.total_liabilities() == 3000 * USDC
    # winner got a DM with the payout, loser did not lose anything extra
    assert any(cid == BOB and "Ты выиграл" in text for cid, text in bot.sent)
    # betting on a resolved market is rejected
    m = Message("/bet 1 1 5", from_id=BOB, bot=bot)
    run(cmd_bet(m))
    assert "закрыта" in m.answers[0][0]


def test_e2e_market_deadline_watcher_and_grace(e2e, monkeypatch):
    install_rpc(monkeypatch, block=1500)
    bot = Bot()
    start(bot, ALICE, "alice")
    start(bot, BOB, "bob")
    fund(e2e, ALICE, 100)
    fund(e2e, BOB, 100)

    past = int(time.time()) - 3600  # deadline already passed
    bid = e2e.create_bet(ALICE, "Просроченный", ["А", "Б"], close_at=past)
    m = Message(f"/bet {bid} 1 10", from_id=BOB, bot=bot)
    run(cmd_bet(m))
    assert "истекло" in m.answers[0][0]  # betting is already closed by deadline

    # market_watcher pings the creator exactly once
    monkeypatch.setattr(botmain, "bot", bot)
    monkeypatch.setattr(config, "POLL_SECONDS", 0.01)

    async def _run_market_watcher():
        t = asyncio.create_task(botmain.market_watcher())
        await bot.evt.wait()
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)

    asyncio.run(_run_market_watcher())
    msgs = [text for cid, text in bot.sent if cid == ALICE]
    assert any("достиг дедлайна" in t and f"#{bid}" in t for t in msgs)
    notified = e2e._conn.execute(
        "SELECT deadline_notified FROM bets WHERE id=%s", (bid,)
    ).fetchone()["deadline_notified"]
    assert notified == 1

    # second run sends nothing new
    bot2 = Bot()
    monkeypatch.setattr(botmain, "bot", bot2)
    async def _run_again():
        t = asyncio.create_task(botmain.market_watcher())
        await asyncio.sleep(0.1)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    asyncio.run(_run_again())
    assert bot2.sent == []

    # grace nearly over (11h left): the watcher sends the final warning once
    e2e._conn.execute(
        "UPDATE bets SET close_at = %s WHERE id = %s", (int(time.time()) - 61 * 3600, bid)
    )
    e2e._conn.commit()
    bot3 = Bot()
    monkeypatch.setattr(botmain, "bot", bot3)
    async def _run_grace():
        t = asyncio.create_task(botmain.market_watcher())
        await bot3.evt.wait()
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    asyncio.run(_run_grace())
    msgs = [text for cid, text in bot3.sent if cid == ALICE]
    assert any(f"#{bid}" in t and "/cancel" in t for t in msgs)
    assert "/cancel" in msgs[0]
    warned = e2e._conn.execute(
        "SELECT grace_warned FROM bets WHERE id=%s", (bid,)
    ).fetchone()["grace_warned"]
    assert warned == 1
    bot4 = Bot()
    monkeypatch.setattr(botmain, "bot", bot4)
    asyncio.run(_run_grace())
    assert bot4.sent == []

    # before grace, only the creator can cancel
    m = Message(f"/cancel {bid}", from_id=BOB, bot=bot)
    run(cmd_cancel(m))
    assert "только создатель" in m.answers[0][0]
    m = Message(f"/cancel {bid}", from_id=ALICE, bot=bot)
    run(cmd_cancel(m))
    assert "деньги возвращены" in m.answers[0][0]
    assert e2e.total_liabilities() == 200 * USDC  # nothing to refund, nothing lost

    # a market with a real position: creator cancels -> backer refunded + DM
    bid2 = e2e.create_bet(ALICE, "Отменяемый", ["А", "Б"], close_at=int(time.time()) + 3600)
    m = Message(f"/bet {bid2} 1 10", from_id=BOB, bot=bot)
    run(cmd_bet(m))
    assert "Ставка принята" in m.answers[0][0]
    assert e2e.balance(BOB) == Decimal("90.000000")
    m = Message(f"/cancel {bid2}", from_id=ALICE, bot=bot)
    run(cmd_cancel(m))
    assert "деньги возвращены" in m.answers[0][0]
    assert e2e.balance(BOB) == Decimal("100.000000")  # refunded
    assert any(cid == BOB and "отменена" in text for cid, text in bot.sent)
    assert e2e.total_liabilities() == 200 * USDC

    # grace passed: ANYONE can refund an abandoned market
    expired = int(time.time()) - config.MARKET_GRACE_HOURS * 3600 - 60
    bid3 = e2e.create_bet(CAROL, "Забытый", ["А", "Б"], close_at=expired)
    fund(e2e, CAROL, 100)
    m = Message(f"/bet {bid3} 1 10", from_id=BOB, bot=bot)
    run(cmd_bet(m))
    m = Message(f"/cancel {bid3}", from_id=BOB, bot=bot)
    run(cmd_cancel(m))
    assert "истёк" in m.answers[0][0]
    assert e2e.balance(BOB) == Decimal("100.000000")  # refunded
    assert e2e.balance(CAROL) == Decimal("100.000000")  # untouched (no stake)
    assert e2e.total_liabilities() == 300 * USDC


# ---------- scenario 3: reaction tips ----------


def test_e2e_reaction_tips(e2e, monkeypatch):
    install_rpc(monkeypatch, block=1500)
    bot = Bot()
    start(bot, ALICE, "alice")
    start(bot, BOB, "bob")
    start(bot, CAROL, "carol")
    fund(e2e, ALICE, 50)

    chat_id = -100123
    # Bob posts, Alice reacts with 🔥 (1 USDC)
    e2e.record_message(chat_id, 1, BOB)

    upd = types.SimpleNamespace(
        user=User(ALICE, "alice"),
        new_reaction=[ReactionTypeEmoji(emoji="🔥")],
        chat=types.SimpleNamespace(id=chat_id),
        message_id=1,
        bot=bot,
    )
    run(on_reaction(upd))
    assert e2e.balance(ALICE) == Decimal("49.000000")
    assert e2e.balance(BOB) == Decimal("1.000000")
    assert any(cid == BOB and "+1" in text for cid, text in bot.sent)
    assert any(cid == ALICE and "Отправлено" in text for cid, text in bot.sent)

    # duplicate reaction on the same message: rejected silently
    run(on_reaction(upd))
    assert e2e.balance(ALICE) == Decimal("49.000000")

    # reacting to your own message: rejected
    upd2 = types.SimpleNamespace(
        user=User(BOB, "bob"),
        new_reaction=[ReactionTypeEmoji(emoji="🔥")],
        chat=types.SimpleNamespace(id=chat_id),
        message_id=1,
        bot=bot,
    )
    run(on_reaction(upd2))
    assert e2e.balance(BOB) == Decimal("1.000000")

    # no balance: rejected + DM prompt
    upd3 = types.SimpleNamespace(
        user=User(CAROL, "carol"),
        new_reaction=[ReactionTypeEmoji(emoji="🔥")],
        chat=types.SimpleNamespace(id=chat_id),
        message_id=1,
        bot=bot,
    )
    run(on_reaction(upd3))
    assert e2e.balance(CAROL) == 0
    assert any(cid == CAROL and "Пополни баланс" in text for cid, text in bot.sent)

    # unsupported emoji / unknown message: no-op
    e2e.record_message(chat_id, 2, BOB)
    upd4 = types.SimpleNamespace(
        user=User(ALICE, "alice"),
        new_reaction=[ReactionTypeEmoji(emoji="😀")],
        chat=types.SimpleNamespace(id=chat_id),
        message_id=2,
        bot=bot,
    )
    run(on_reaction(upd4))
    assert e2e.balance(ALICE) == Decimal("49.000000")

    # highest-value emoji wins (❤️=2 > 🔥=1)
    e2e.record_message(chat_id, 3, BOB)
    upd5 = types.SimpleNamespace(
        user=User(ALICE, "alice"),
        new_reaction=[ReactionTypeEmoji(emoji="🔥"), ReactionTypeEmoji(emoji="❤️")],
        chat=types.SimpleNamespace(id=chat_id),
        message_id=3,
        bot=bot,
    )
    run(on_reaction(upd5))
    assert e2e.balance(ALICE) == Decimal("47.000000")

    # conservation
    assert e2e.total_liabilities() == 50 * USDC


# ---------- scenario 4: deep links ----------


def test_e2e_deep_link_bet_market(e2e, monkeypatch):
    install_rpc(monkeypatch, block=1500)
    bot = Bot()
    start(bot, ALICE, "alice")
    start(bot, BOB, "bob")
    start(bot, CAROL, "carol")
    fund(e2e, ALICE, 100)
    fund(e2e, BOB, 100)
    fund(e2e, CAROL, 100)
    bid = e2e.create_bet(ALICE, "Кто победит?", ["А", "Б"])
    m = Message(f"/bet {bid} 1 10", from_id=BOB, bot=bot)
    run(cmd_bet(m))

    # a fresh user comes through the shared market link
    m = Message("/start", from_id=CAROL, username="carol", bot=bot)
    run(cmd_start(m, CommandObject(command="start", args=f"bet_{bid}")))
    assert "Ты пришёл по ссылке" in m.answers[0][0]
    kb = m.answers[0][1]
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert f"betq:{bid}:0" in data and f"betq:{bid}:1" in data

    # the inline flow works end to end: pick option -> amount -> place
    cb = Callback(f"betq:{bid}:0", CAROL, bot=bot)
    run(cb_bet_amount(cb))
    assert cb.message.text and "сколько ставим" in cb.message.text
    cb2 = Callback(f"bets:{bid}:0:25", CAROL, bot=bot)
    run(cb_bet_place(cb2))
    assert "Ставка принята" in cb2.message.text
    assert e2e.balance(CAROL) == Decimal("75.000000")


def test_e2e_deep_link_resolved_and_unknown(e2e, monkeypatch):
    install_rpc(monkeypatch, block=1500)
    bot = Bot()
    start(bot, ALICE, "alice")
    fund(e2e, ALICE, 100)
    bid = e2e.create_bet(ALICE, "Решённый", ["А", "Б"])
    e2e.place_bet(bid, ALICE, 0, 10 * USDC)
    e2e.resolve_bet(bid, 0, ALICE)

    m = Message("/start", from_id=BOB, username="bob", bot=bot)
    run(cmd_start(m, CommandObject(command="start", args=f"bet_{bid}")))
    assert "Решён" in m.answers[0][0]  # the card shows the outcome

    m = Message("/start", from_id=BOB, username="bob", bot=bot)
    run(cmd_start(m, CommandObject(command="start", args="bet_999999")))
    assert "не найден" in m.answers[0][0]

    # donate deep link opens the landing page with QR
    m = Message("/start", from_id=BOB, username="bob", bot=bot)
    run(cmd_start(m, CommandObject(command="start", args=f"donate_{ALICE}")))
    assert m.photos and "Поддержать" in m.photos[0][1]

    # unknown args fall back to the greeting
    m = Message("/start", from_id=BOB, username="bob", bot=bot)
    run(cmd_start(m, CommandObject(command="start", args="garbage")))
    assert "Привет" in m.answers[0][0]


# ---------- scenario 5: wallet linking security ----------


def test_e2e_wallet_security(e2e, monkeypatch):
    bot = Bot()
    start(bot, ALICE, "alice")
    start(bot, BOB, "bob")

    # link flow with the WRONG signature
    m = Message(f"/link {ACC.address}", from_id=ALICE, bot=bot)
    run(cmd_link(m))
    row = e2e.get_link_nonce(ALICE)
    sign_text = f"Tippy: link {ALICE}:{row['nonce']}"
    wrong_sig = ACC2.sign_message(encode_defunct(text=sign_text)).signature.hex()
    m = Message(f"/confirm 0x{wrong_sig}", from_id=ALICE, bot=bot)
    run(cmd_confirm(m))
    assert "Подпись не совпадает" in m.answers[0][0]
    assert e2e.linked_address(ALICE) is None

    # garbage signature rejected at the format gate
    m = Message("/confirm 0x1234", from_id=ALICE, bot=bot)
    run(cmd_confirm(m))
    assert "Формат" in m.answers[0][0]

    # confirm without a link attempt
    m = Message(f"/confirm 0x{wrong_sig}", from_id=BOB, bot=bot)
    run(cmd_confirm(m))
    assert "Сначала начни привязку" in m.answers[0][0]

    # link with the RIGHT signature
    sig = ACC.sign_message(encode_defunct(text=sign_text)).signature.hex()
    m = Message(f"/confirm 0x{sig}", from_id=ALICE, bot=bot)
    run(cmd_confirm(m))
    assert "привязан" in m.answers[0][0]

    # foreign deposit cannot be claimed by a non-owner
    tx = "0x" + "bb" * 32
    e2e.record_pending(tx, ACC2.address, 5 * USDC)
    m = Message(f"/claim {tx}", from_id=BOB, bot=bot)
    run(cmd_claim(m))
    assert "только владелец" in m.answers[0][0]
    assert e2e.balance(BOB) == 0

    # the actual owner (BOB links ACC2) — the deposit is auto-claimed at once
    m = Message(f"/link {ACC2.address}", from_id=BOB, bot=bot)
    run(cmd_link(m))
    row = e2e.get_link_nonce(BOB)
    sig = ACC2.sign_message(encode_defunct(text=f"Tippy: link {BOB}:{row['nonce']}")).signature.hex()
    m = Message(f"/confirm 0x{sig}", from_id=BOB, bot=bot)
    run(cmd_confirm(m))
    assert "привязан" in m.answers[0][0]
    assert "Сразу зачислено: 1" in m.answers[0][0]
    assert e2e.balance(BOB) == Decimal("5.000000")  # auto-claimed by the link

    # manual /claim for the same tx: already credited
    m = Message(f"/claim {tx}", from_id=BOB, bot=bot)
    run(cmd_claim(m))
    assert "уже зачислена" in m.answers[0][0]

    # malformed /claim
    m = Message("/claim not-a-hash", from_id=ALICE, bot=bot)
    run(cmd_claim(m))
    assert "Формат" in m.answers[0][0]

    # expired link nonce rejected (a fresh user who was never linked)
    start(bot, CAROL, "carol")
    m = Message(f"/link {ACC2.address}", from_id=CAROL, bot=bot)
    run(cmd_link(m))
    row = e2e.get_link_nonce(CAROL)
    e2e._conn.execute(
        "UPDATE link_nonces SET created_at = %s WHERE tg_id = %s",
        (int(time.time()) - config.LINK_NONCE_TTL_SECONDS - 10, CAROL),
    )
    e2e._conn.commit()
    sig = ACC2.sign_message(encode_defunct(text=f"Tippy: link {CAROL}:{row['nonce']}")).signature.hex()
    m = Message(f"/confirm 0x{sig}", from_id=CAROL, bot=bot)
    run(cmd_confirm(m))
    assert "устарел" in m.answers[0][0]
    assert e2e.linked_address(CAROL) is None

    # linking auto-claims pending deposits from that address
    tx2 = "0x" + "cc" * 32
    e2e.record_pending(tx2, ACC2.address, 7 * USDC)
    m = Message(f"/link {ACC2.address}", from_id=BOB, bot=bot)
    run(cmd_link(m))
    row = e2e.get_link_nonce(BOB)
    sig = ACC2.sign_message(encode_defunct(text=f"Tippy: link {BOB}:{row['nonce']}")).signature.hex()
    m = Message(f"/confirm 0x{sig}", from_id=BOB, bot=bot)
    run(cmd_confirm(m))
    assert "Сразу зачислено: 1" in m.answers[0][0]
    assert e2e.balance(BOB) == Decimal("12.000000")  # 5 claimed earlier + 7 auto


# ---------- scenario 6: dashboard values on real data ----------


def test_e2e_dashboard_values(e2e, monkeypatch, api):
    bot = Bot()
    install_rpc(monkeypatch, block=1500)
    e2e.set_last_block(1500)
    for tg, name in ((ALICE, "alice"), (BOB, "bob"), (CAROL, "carol")):
        start(bot, tg, name)

    # deposit 100 (no wallet link -> stays pending), then link to claim
    tx = "0x" + "dd" * 32
    e2e.record_pending(tx, ACC.address, 100 * USDC)
    m = Message(f"/link {ACC.address}", from_id=ALICE, bot=bot)
    run(cmd_link(m))
    row = e2e.get_link_nonce(ALICE)
    sig = ACC.sign_message(encode_defunct(text=f"Tippy: link {ALICE}:{row['nonce']}")).signature.hex()
    m = Message(f"/confirm 0x{sig}", from_id=ALICE, bot=bot)
    run(cmd_confirm(m))

    # tip 5, bet 10+5 on a market
    m = Message("/tip 5 @bob", from_id=ALICE, bot=bot)
    run(cmd_tip(m))
    bid = e2e.create_bet(CAROL, "Вопрос?", ["А", "Б"])
    m = Message(f"/bet {bid} 1 10", from_id=ALICE, bot=bot)
    run(cmd_bet(m))
    m = Message(f"/bet {bid} 2 5", from_id=BOB, bot=bot)
    run(cmd_bet(m))

    # stats: exact values
    s = api.get("/api/stats").json()
    assert s["users"] == 3
    assert s["open_markets"] == 1
    assert s["tips_usdc"] == 5.0
    assert s["deposits_usdc"] == 100.0
    assert s["bets_usdc"] == 15.0
    assert s["volume_usdc"] == 120.0
    assert s["transactions"] == 4  # deposit, tip, 2 bets

    # market view: pools and probabilities are exact
    mv = api.get(f"/api/market/{bid}").json()
    assert mv["pot_usdc"] == 15.0
    pools = {o["index"]: o["pool_usdc"] for o in mv["options"]}
    assert pools == {0: 10.0, 1: 5.0}
    assert mv["creator"]["username"] == "carol"

    # user view: positions, history, balance (owner-only data -> as ALICE)
    api.cookies.set(COOKIE_NAME, make_session(ALICE))
    u = api.get(f"/api/user/{ALICE}").json()
    assert u["is_owner"] is True
    assert u["balance_usdc"] == 85.0  # 100 - 5 tip - 10 bet
    assert u["tips_sent_usdc"] == 5.0
    assert [p["bet_id"] for p in u["positions"]] == [bid]
    assert u["positions"][0]["stake_usdc"] == 10.0
    kinds = [h["kind"] for h in u["history"]]
    assert kinds == ["bet", "tip", "deposit"]

    # leaderboard: Alice top tipper
    lb = api.get("/api/leaderboard").json()
    assert lb[0]["total_usdc"] == 5.0 and lb[0]["username"] == "alice"

    # solvency: hot balance 500 covers liabilities + pending.
    # liabilities now correctly include the open bet pool (15 USDC) on top of
    # user balances (85 USDC): 100 total.
    sol = api.get("/api/solvency").json()
    assert sol["liabilities_usdc"] == 100.0  # 85 user bal + 15 bet pool
    assert sol["pending_deposits_usdc"] == 0.0
    assert sol["owed_usdc"] == 100.0
    assert sol["hot_wallet_balance_usdc"] == 500.0
    assert sol["solvent"] is True
    assert sol["reserve_usdc"] == 400.0

    # health with the fake RPC chain
    h = api.get("/api/health").json()
    assert h["chain_head"] == 1500 and h["last_scanned_block"] == 1500
    assert h["deposit_lag"] == 0

    # 404s for unknown entities
    assert api.get("/api/user/424242").status_code == 404
    assert api.get("/api/market/999").status_code == 404
    assert api.get("/qr?data=").status_code == 400  # missing param
    assert api.get("/qr?data=" + "x" * 2000).status_code == 400  # too long


# ---------- scenario 7: failure handling ----------


def test_e2e_rpc_outage_watchers_survive(e2e, monkeypatch):
    bot = Bot()
    start(bot, ALICE, "alice")
    nonce = e2e.new_link_nonce(ALICE, ACC.address)
    assert e2e.confirm_link(ALICE, ACC.address, nonce)
    monkeypatch.setattr(botmain, "bot", bot)
    monkeypatch.setattr(config, "POLL_SECONDS", 0.01)

    # RPC fails on the first call, then recovers; watcher must survive
    tx = "0x" + "ee" * 32
    log = _transfer_log(ACC.address, str(base.hot_wallet()), 10 * USDC, tx)
    install_rpc(monkeypatch, logs=[log], block=1200, fail_first=1)

    async def _run():
        t = asyncio.create_task(botmain.deposit_watcher())
        await bot.evt.wait()
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    asyncio.run(_run())
    assert any("Депозит зачислен" in text for _, text in bot.sent)


def test_e2e_withdraw_refund_paths(e2e, monkeypatch):
    install_rpc(monkeypatch, block=1500)
    bot = Bot()
    start(bot, ALICE, "alice")
    fund(e2e, ALICE, 100)
    to_addr = ACC2.address

    # 1) send fails on-chain (after broadcast) -> row stays pending with the
    # pre-computed tx hash; the watcher settles from the real receipt later.
    # NEVER an immediate refund — that would double-pay if the tx confirmed.
    m = Message(f"/withdraw {to_addr} 10", from_id=ALICE, bot=bot)

    def boom(raw):
        raise ConnectionError("no gas")
    base.w3.eth.send_raw_transaction = boom
    run(cmd_withdraw(m))
    assert "⏳" in m.answers[0][0]  # BroadcastUncertainError message
    assert e2e.balance(ALICE) == Decimal("89.900000")  # debited (amount+fee), not refunded
    assert len(e2e.pending_withdraws()) == 1  # pending with tx hash

    # 2) pending with no tx_hash, past the stuck timeout -> refunded by watcher
    cur = e2e._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, status, created_at) "
        "VALUES ('withdraw', %s, %s, %s, 'pending', %s) RETURNING id",
        (ALICE, to_addr, 5 * USDC, int(time.time()) - config.WITHDRAW_STUCK_TIMEOUT_SECONDS - 10),
    )
    e2e._conn.commit()
    wd_id = cur.fetchone()["id"]
    e2e._conn.execute("UPDATE users SET balance = balance - %s WHERE tg_id = %s", (5 * USDC + 50_000, ALICE))
    e2e._conn.commit()
    asyncio.run(base.check_pending_withdraws())
    assert e2e.balance(ALICE) == Decimal("89.900000")  # step1 pending still debited; step2 refunded
    assert e2e._conn.execute("SELECT status FROM tx_log WHERE id=%s", (wd_id,)).fetchone()["status"] == "refunded"

    # 3) receipt status=0 (reverted) -> refund
    tx3 = "0x" + "ff" * 32
    cur = e2e._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, status, created_at) "
        "VALUES ('withdraw', %s, %s, %s, %s, 'pending', %s) RETURNING id",
        (ALICE, to_addr, 5 * USDC, tx3, int(time.time()) - 10),
    )
    e2e._conn.commit()
    wd_id3 = cur.fetchone()["id"]
    e2e._conn.execute("UPDATE users SET balance = balance - %s WHERE tg_id = %s", (5 * USDC + 50_000, ALICE))
    e2e._conn.commit()
    install_rpc(monkeypatch, block=1500, receipts={tx3: {"status": 0}})
    asyncio.run(base.check_pending_withdraws())
    assert e2e.balance(ALICE) == Decimal("89.900000")  # step1 pending; step3 reverted->refunded

    # 4) receipt status=1 -> done, no refund
    tx4 = "0x" + "11" * 32
    cur = e2e._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, status, created_at) "
        "VALUES ('withdraw', %s, %s, %s, %s, 'pending', %s) RETURNING id",
        (ALICE, to_addr, 5 * USDC, tx4, int(time.time()) - 10),
    )
    e2e._conn.commit()
    wd_id4 = cur.fetchone()["id"]
    e2e._conn.execute("UPDATE users SET balance = balance - %s WHERE tg_id = %s", (5 * USDC + 50_000, ALICE))
    e2e._conn.commit()
    install_rpc(monkeypatch, block=1500, receipts={tx4: {"status": 1}})
    asyncio.run(base.check_pending_withdraws())
    assert e2e._conn.execute("SELECT status FROM tx_log WHERE id=%s", (wd_id4,)).fetchone()["status"] == "done"
    assert e2e.balance(ALICE) == Decimal("84.850000")  # 89.9 minus 5.05 debited at creation, no refund

    # 5) receipt still missing but not timed out -> still pending
    tx5 = "0x" + "22" * 32
    cur = e2e._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, status, created_at) "
        "VALUES ('withdraw', %s, %s, %s, %s, 'pending', %s) RETURNING id",
        (ALICE, to_addr, 5 * USDC, tx5, int(time.time()) - 10),
    )
    e2e._conn.commit()
    e2e._conn.execute("UPDATE users SET balance = balance - %s WHERE tg_id = %s", (5 * USDC + 50_000, ALICE))
    e2e._conn.commit()
    install_rpc(monkeypatch, block=1500, receipts={})
    asyncio.run(base.check_pending_withdraws())
    assert e2e.balance(ALICE) == Decimal("79.800000")  # step1 pending; step5 not timed out yet

    # 6) legacy NULL-status row with tx_hash -> marked done, never refunded
    tx6 = "0x" + "33" * 32
    e2e._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, status, created_at) "
        "VALUES ('withdraw', %s, %s, %s, %s, NULL, %s)",
        (ALICE, to_addr, 5 * USDC, tx6, int(time.time()) - 3600),
    )
    e2e._conn.commit()
    asyncio.run(base.check_pending_withdraws())
    rows = e2e._conn.execute(
        "SELECT status FROM tx_log WHERE tx_hash=%s AND kind='withdraw'", (tx6,)
    ).fetchall()
    assert all(r["status"] == "done" for r in rows)


def test_e2e_insufficient_and_limits(e2e, monkeypatch):
    install_rpc(monkeypatch, block=1500)
    bot = Bot()
    start(bot, ALICE, "alice")
    start(bot, BOB, "bob")

    # tip without balance
    m = Message("/tip 5 @bob", from_id=ALICE, bot=bot)
    run(cmd_tip(m))
    assert "Недостаточно" in m.answers[0][0]

    # tip to self
    fund(e2e, ALICE, 10)
    m = Message("/tip 1 @alice", from_id=ALICE, bot=bot)
    run(cmd_tip(m))
    assert "Себе" in m.answers[0][0]

    # tip to unknown user
    m = Message("/tip 1 @nobody", from_id=ALICE, bot=bot)
    run(cmd_tip(m))
    assert "Не нашёл" in m.answers[0][0]

    # tip over the max
    m = Message(f"/tip {config.MAX_TIP_USDC + 1} @bob", from_id=ALICE, bot=bot)
    run(cmd_tip(m))
    assert "Максимум" in m.answers[0][0]

    # tip without recipient
    m = Message("/tip 5", from_id=ALICE, bot=bot)
    run(cmd_tip(m))
    assert "Кому" in m.answers[0][0]

    # withdraw without balance
    m = Message(f"/withdraw {ACC2.address} 50", from_id=BOB, bot=bot)
    run(cmd_withdraw(m))
    assert "Недостаточно" in m.answers[0][0]

    # withdraw below minimum
    m = Message(f"/withdraw {ACC2.address} 0.1", from_id=ALICE, bot=bot)
    run(cmd_withdraw(m))
    assert "Минимум" in m.answers[0][0]

    # withdraw malformed address
    m = Message("/withdraw 0x123 5", from_id=ALICE, bot=bot)
    run(cmd_withdraw(m))
    assert "Формат" in m.answers[0][0]

    # bet on a nonexistent market
    m = Message("/bet 999 1 5", from_id=ALICE, bot=bot)
    run(cmd_bet(m))
    assert "не найдена" in m.answers[0][0]

    # bet with invalid option
    fund(e2e, BOB, 10)
    bid = e2e.create_bet(ALICE, "Q", ["А", "Б"])
    m = Message(f"/bet {bid} 9 5", from_id=BOB, bot=bot)
    run(cmd_bet(m))
    assert "Неверный номер" in m.answers[0][0]

    # bet without balance
    m = Message(f"/bet {bid} 1 5", from_id=CAROL, bot=bot)
    run(cmd_bet(m))
    assert "Недостаточно" in m.answers[0][0]

    # resolve by a non-creator
    fund(e2e, CAROL, 10)
    m = Message(f"/bet {bid} 1 5", from_id=BOB, bot=bot)
    run(cmd_bet(m))
    m = Message(f"/resolve {bid} 1", from_id=BOB, bot=bot)
    run(cmd_resolve(m))
    assert "только создатель" in m.answers[0][0]

    # resolve with nobody on the winning option
    m = Message(f"/resolve {bid} 2", from_id=ALICE, bot=bot)
    run(cmd_resolve(m))
    assert "Никто не поставил" in m.answers[0][0]

    # resolve empty market
    bid2 = e2e.create_bet(ALICE, "Пустой", ["А", "Б"])
    m = Message(f"/resolve {bid2} 1", from_id=ALICE, bot=bot)
    run(cmd_resolve(m))
    assert "нет денег" in m.answers[0][0]

    # malformed commands (junk args are ignored by /history, so it is not in the list)
    for text in ("/bet", "/bet x y z", "/resolve 1", "/cancel", "/claim", "/link 0x1"):
        m = Message(text, from_id=ALICE, bot=bot)
        cmd = text.split()[0].lstrip("/")
        fn = {"bet": cmd_bet, "resolve": cmd_resolve, "cancel": cmd_cancel,
              "claim": cmd_claim, "link": cmd_link, "history": cmd_history}[cmd]
        run(fn(m))
        assert "Формат" in m.answers[0][0]


def test_e2e_dashboard_rate_limit_and_static(e2e, monkeypatch, api):
    monkeypatch.setattr(web_server, "WEB_RATE_LIMIT", 2)
    api.get("/api/health")
    api.get("/api/health")
    r = api.get("/api/health")
    assert r.status_code == 429
    # static pages are never rate limited
    assert api.get("/").status_code == 200
    assert api.get("/m/1").status_code == 200


# ---------- scenario 8: concurrency ----------


def test_e2e_concurrent_transfers_and_bets(e2e):
    n_users = 8
    for u in range(1, n_users + 1):
        fund(e2e, u, 100)
    initial = e2e.total_liabilities()
    bid = e2e.create_bet(1, "Кто?", ["А", "Б", "В"])
    errors = []

    def worker(i):
        try:
            for _ in range(60):
                a = (i * 7 + _) % n_users + 1
                b = (i * 13 + _ * 3) % n_users + 1
                if a != b:
                    e2e.transfer(a, b, 1_000_000)
                if _ % 3 == 0:
                    e2e.place_bet(bid, a, _ % 3, 1_000_000)
        except Exception as exc:  # pragma: no cover - assertion aid
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # total_liabilities now includes both user balances AND the open bet pool,
    # so a bet moving money from a user's balance into positions leaves the
    # grand total unchanged. Conservation => final liabilities == initial.
    positions = e2e._conn.execute(
        "SELECT COALESCE(SUM(amount_micro), 0) AS s FROM bet_positions WHERE bet_id=%s", (bid,)
    ).fetchone()["s"]
    assert e2e.total_liabilities() == initial
    # no user went negative
    negatives = e2e._conn.execute("SELECT COUNT(*) AS c FROM users WHERE balance < 0").fetchone()["c"]
    assert negatives == 0


# ---------- scenario 9: all three watchers together ----------


def test_e2e_all_watchers_run_together(e2e, monkeypatch):
    bot = Bot()
    start(bot, ALICE, "alice")
    start(bot, BOB, "bob")
    fund(e2e, BOB, 10)
    nonce = e2e.new_link_nonce(ALICE, ACC.address)
    assert e2e.confirm_link(ALICE, ACC.address, nonce)

    # pending withdraw that will be refunded by the watcher
    to_addr = ACC2.address
    e2e._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, status, created_at) "
        "VALUES ('withdraw', %s, %s, %s, 'pending', %s)",
        (BOB, to_addr, 2 * USDC, int(time.time()) - config.WITHDRAW_STUCK_TIMEOUT_SECONDS - 10),
    )
    e2e._conn.commit()
    e2e._conn.execute("UPDATE users SET balance = balance - %s WHERE tg_id = %s", (2 * USDC + 20_000, BOB))
    e2e._conn.commit()

    # overdue market for the market watcher
    past = int(time.time()) - 3600
    bid = e2e.create_bet(ALICE, "Просрочка", ["А", "Б"], close_at=past)

    # deposit for the deposit watcher
    tx = "0x" + "77" * 32
    log = _transfer_log(ACC.address, str(base.hot_wallet()), 30 * USDC, tx)
    install_rpc(monkeypatch, logs=[log], block=1400, fail_first=1)  # first poll fails

    monkeypatch.setattr(botmain, "bot", bot)
    monkeypatch.setattr(config, "POLL_SECONDS", 0.01)

    async def _run_all():
        tasks = [
            asyncio.create_task(botmain.deposit_watcher()),
            asyncio.create_task(botmain.withdraw_watcher()),
            asyncio.create_task(botmain.market_watcher()),
        ]
        await bot.evt.wait()  # first DM (deposit)
        await asyncio.sleep(0.2)  # let the others do their cycles
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(_run_all())

    texts = [text for _, text in bot.sent]
    assert any("Депозит зачислен" in t for t in texts)          # deposit watcher survived the RPC outage
    assert any("достиг дедлайна" in t for t in texts)            # market watcher pinged
    assert e2e.balance(BOB) == Decimal("10.000000")              # withdraw watcher refunded
    assert e2e.balance(ALICE) == Decimal("30.000000")            # deposit credited
    # no double messages: exactly one deposit DM, one deadline DM
    assert sum("Депозит зачислен" in t for t in texts) == 1
    assert sum("достиг дедлайна" in t for t in texts) == 1
