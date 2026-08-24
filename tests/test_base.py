"""Base (web3) layer tests with mocked RPC. Crypto (signing) is real."""

import asyncio
import os
import time
import types
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address
from hexbytes import HexBytes
from web3 import Web3

from bot import base

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5432/tipbot_test"
)


@pytest.fixture()
def real_account():
    return Account.from_key("0x" + "22" * 32)


def _real_eth():
    return base.w3.eth.account  # real local crypto, no network


def _to_wei(value, unit):
    from decimal import Decimal as D

    units = {"wei": 1, "gwei": 10**9, "ether": 10**18}
    return int(D(str(value)) * D(units[unit]))


def _fake_w3(monkeypatch, block=1000, get_logs=None, tx_count=5, base_fee=1_000_000_000):
    eth = types.SimpleNamespace(
        account=_real_eth(),
        block_number=block,
        chain_id=8453,
        get_logs=get_logs or (lambda q: []),
        get_transaction_count=lambda a, p: tx_count,
        get_block=lambda x: {"baseFeePerGas": base_fee},
        send_raw_transaction=lambda raw: b"\x01" * 32,
    )
    fake = types.SimpleNamespace(eth=eth, to_wei=_to_wei)
    monkeypatch.setattr(base, "w3", fake)
    return fake


def test_hot_wallet_is_checksummed():
    assert base.hot_wallet().startswith("0x")
    assert base.hot_wallet() == to_checksum_address(base.hot_wallet())


def test_recover_signer_roundtrip(real_account):
    msg = "Tippy: link 42:deadbeef"
    sig = base.w3.eth.account.sign_message(
        encode_defunct(text=msg), private_key=real_account.key
    ).signature.hex()
    assert asyncio.run(base.recover_signer(msg, sig)).lower() == real_account.address.lower()


@pytest.mark.parametrize(
    "micro,fee",
    [(1_000_000, 10_000), (1_000, 10), (100, 1), (1, 1), (50, 1), (1_234_567, 12_346)],
)
def test_withdraw_fee_ceiling_and_min(micro, fee):
    assert base.withdraw_fee(micro) == fee


def test_hot_balance_reads_contract(monkeypatch):
    class FakeFunctions:
        def balanceOf(self, addr):
            self.addr = addr
            return self

        def call(self):
            return 5_000_000

    fake_usdc = types.SimpleNamespace(functions=FakeFunctions())
    monkeypatch.setattr(base, "usdc", fake_usdc)
    assert asyncio.run(base.hot_balance()) == 5.0


def test_vault_balance_none_without_vault(monkeypatch):
    monkeypatch.setattr(base.config, "VAULT_ADDRESS", None)
    assert asyncio.run(base.vault_balance()) is None


def test_vault_balance_reads_vault_contract(monkeypatch):
    class FakeFunctions:
        def balanceOf(self, addr):
            self.addr = addr
            return self

        def call(self):
            return 42_500_000

    fake_usdc = types.SimpleNamespace(functions=FakeFunctions())
    monkeypatch.setattr(base, "usdc", fake_usdc)
    monkeypatch.setattr(base.config, "VAULT_ADDRESS", "0x" + "ab" * 20)
    assert asyncio.run(base.vault_balance()) == 42.5
    assert fake_usdc.functions.addr.lower() == "0x" + "ab" * 20


def test_scan_deposits_returns_clean_events(monkeypatch):
    class FakeEth:
        def __init__(self):
            self.logs = [
                {
                    "transactionHash": HexBytes(b"\xaa" * 32),
                    "sender": "0x1111",
                    "value": 123456,
                },
                {
                    "transactionHash": HexBytes(b"\xbb" * 32),
                    "sender": base.EDGE_1,
                    "value": 999,  # mint — must be skipped
                },
                {
                    "transactionHash": HexBytes(b"\xcc" * 32),
                    "sender": "0x2222",
                    "value": 7,
                },
            ]

        def get_logs(self, q):
            return self.logs

    eth = FakeEth()
    fake_w3 = types.SimpleNamespace(eth=eth, to_wei=lambda v, u: v)
    monkeypatch.setattr(base, "w3", fake_w3)

    class FakeEvent:
        def process_log(self, log):
            return {"args": {"from": log["sender"], "value": log["value"]}}

    class FakeEvents:
        def Transfer(self):
            return FakeEvent()

    monkeypatch.setattr(base, "usdc", types.SimpleNamespace(events=FakeEvents()))

    deps = base._scan_deposits(1, 10)
    assert len(deps) == 2  # mint skipped
    assert deps[0]["tx_hash"] == "0x" + "aa" * 32
    assert deps[0]["amount_micro"] == 123456


def test_scan_deposits_skips_unparsable_logs(monkeypatch):
    class FakeEth:
        def get_logs(self, q):
            return [{"transactionHash": HexBytes(b"\xdd" * 32)}]  # no args -> process fails

    fake_w3 = types.SimpleNamespace(eth=FakeEth(), to_wei=lambda v, u: v)
    monkeypatch.setattr(base, "w3", fake_w3)

    class BoomEvent:
        def process_log(self, log):
            raise ValueError("bad log")

    monkeypatch.setattr(base, "usdc", types.SimpleNamespace(events=types.SimpleNamespace(Transfer=lambda: BoomEvent())))
    assert base._scan_deposits(1, 10) == []


def test_scan_deposits_real_abi_decode(monkeypatch):
    """Only the RPC call (get_logs) is faked; event decoding is the real web3 ABI."""
    from eth_utils import keccak

    topic0 = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
    from_ = "0x" + "11" * 20
    value = 7_777_777
    log = {
        "address": base.USDC,
        "topics": [
            topic0,
            "0x" + "00" * 12 + from_[2:],
            "0x" + "00" * 12 + base.HOT_WALLET[2:].lower(),
        ],
        "data": Web3.to_hex(value.to_bytes(32, "big")),
        "blockNumber": 500,
        "blockHash": HexBytes(b"\x00" * 32),
        "transactionHash": HexBytes(b"\xab" * 32),
        "transactionIndex": 0,
        "logIndex": 0,
    }

    class FakeEth:
        def get_logs(self, q):
            return [log]

    monkeypatch.setattr(
        base, "w3", types.SimpleNamespace(eth=FakeEth(), to_wei=lambda v, u: v)
    )
    deps = base._scan_deposits(1, 10)
    assert deps == [
        {
            "tx_hash": "0x" + "ab" * 32,
            "sender": Web3.to_checksum_address(from_),
            "amount_micro": value,
        }
    ]

def test_poll_deposits_first_run(monkeypatch):
    fake_w3 = _fake_w3(monkeypatch, block=1000)
    seen = {}

    class FakeLedger:
        def last_block(self):
            return 0

        def set_last_block(self, b):
            seen["last"] = b

        def x402_paid(self, tx):
            return False

        def record_pending(self, tx, sender, amount):
            seen["pending"] = (tx, sender, amount)

        def tg_id_of_address(self, addr):
            return None

    monkeypatch.setattr(base, "ledger", FakeLedger())

    scanned = []

    def fake_scan(f, t):
        scanned.append((f, t))
        return [{"tx_hash": "0x" + "ee" * 32, "sender": "0xabc", "amount_micro": 42}]

    monkeypatch.setattr(base, "_scan_deposits", fake_scan)
    asyncio.run(base.poll_deposits())
    # Cold start backfills from the lookback window (>= 2000 blocks), clamped to block 1.
    assert scanned == [(1, 1000)]
    assert seen["last"] == 1000
    assert seen["pending"] == ("0x" + "ee" * 32, "0xabc", 42)


def test_poll_deposits_skips_x402_paid_tx(monkeypatch):
    _fake_w3(monkeypatch, block=1000)
    seen = {}

    class FakeLedger:
        def last_block(self):
            return 0

        def set_last_block(self, b):
            seen["last"] = b

        def x402_paid(self, tx):
            return tx == "0x" + "ee" * 32  # this tx was already an x402 tip

        def record_pending(self, tx, sender, amount):
            seen["pending"] = (tx, sender, amount)

        def tg_id_of_address(self, addr):
            return None

    monkeypatch.setattr(base, "ledger", FakeLedger())
    monkeypatch.setattr(
        base,
        "_scan_deposits",
        lambda f, t: [
            {"tx_hash": "0x" + "ee" * 32, "sender": "0xabc", "amount_micro": 42},
            {"tx_hash": "0x" + "ff" * 32, "sender": "0xdef", "amount_micro": 7},
        ],
    )
    asyncio.run(base.poll_deposits())
    assert seen["pending"] == ("0x" + "ff" * 32, "0xdef", 7)  # only the fresh one


def test_poll_deposits_no_new_blocks(monkeypatch):
    fake_w3 = _fake_w3(monkeypatch, block=1000)

    class FakeLedger:
        def last_block(self):
            return 1000

    monkeypatch.setattr(base, "ledger", FakeLedger())

    def fake_scan(f, t):
        raise AssertionError("must not scan")

    monkeypatch.setattr(base, "_scan_deposits", fake_scan)
    asyncio.run(base.poll_deposits())  # current == last -> early return


def test_poll_deposits_rescans_recent_blocks_for_reorg(monkeypatch):
    fake_w3 = _fake_w3(monkeypatch, block=500)
    scanned = []
    seen = {}

    class FakeLedger:
        def last_block(self):
            return 495  # only 5 new blocks -> re-scan overlaps the confirm window

        def set_last_block(self, b):
            seen["last"] = b

        def record_pending(self, tx, sender, amount):
            pass

        def tg_id_of_address(self, addr):
            return None

    monkeypatch.setattr(base, "ledger", FakeLedger())
    monkeypatch.setattr(
        base,
        "_scan_deposits",
        lambda f, t: (scanned.append((f, t)), [])[1],
    )
    asyncio.run(base.poll_deposits())
    # start clamps back into the confirm window (block 490), re-scanning 490-500.
    assert scanned == [(490, 500)]
    assert seen["last"] == 500


def test_poll_deposits_auto_claims_linked(monkeypatch):
    fake_w3 = _fake_w3(monkeypatch, block=500)
    calls = []
    seen = {}

    class FakeLedger:
        def last_block(self):
            return 300

        def set_last_block(self, b):
            seen["last"] = b

        def x402_paid(self, tx):
            return False

        def record_pending(self, tx, sender, amount):
            pass

        def tg_id_of_address(self, addr):
            return 777 if addr == "0xowner" else None

        def claim_for_sender(self, tg_id, sender):
            calls.append((tg_id, sender))
            return [{"tx_hash": "0x1", "amount_micro": 5}]

    monkeypatch.setattr(base, "ledger", FakeLedger())
    monkeypatch.setattr(
        base,
        "_scan_deposits",
        lambda f, t: [{"tx_hash": "0x1", "sender": "0xowner", "amount_micro": 5}],
    )
    credited = asyncio.run(base.poll_deposits())
    assert calls == [(777, "0xowner")]
    assert seen["last"] == 500
    # The sweep returns exactly what was credited so the watcher can notify.
    assert credited == [{"tg_id": 777, "amount_micro": 5, "tx_hash": "0x1"}]


def test_send_usdc_builds_and_sends(monkeypatch):
    _fake_w3(monkeypatch, tx_count=7, base_fee=1_000_000_000)
    captured = {}

    class FakeTransfer:
        def __init__(self):
            self.kwargs = None

        def build_transaction(self, kwargs):
            self.kwargs = kwargs
            return {**kwargs, "data": b"x", "gas": 60000, "chainId": 8453}

    transfer = FakeTransfer()

    class FakeFunctions:
        def transfer(self, to, amount):
            captured["to"] = to
            captured["amount"] = amount
            return transfer

    monkeypatch.setattr(base, "usdc", types.SimpleNamespace(functions=FakeFunctions()))
    tx_hash = asyncio.run(base.send_usdc("0x" + "33" * 20, 123456))
    assert tx_hash == "0x" + "01" * 32
    assert captured["to"] == to_checksum_address("0x" + "33" * 20)
    assert captured["amount"] == 123456
    assert transfer.kwargs["nonce"] == 7
    assert transfer.kwargs["from"] == base.HOT_WALLET
    assert transfer.kwargs["maxPriorityFeePerGas"] == 10_000_000  # 0.01 gwei
    assert transfer.kwargs["maxFeePerGas"] == 2_000_000_000 + 10_000_000  # base*2 + tip


# ---------- pending-withdraw watcher ----------


def _reset_db(ledger) -> None:
    ledger._conn.execute(
        "TRUNCATE users, tx_log, pending_deposits, link_nonces, wallet_links, "
        "bets, bet_positions, last_block, message_authors, reaction_tips, "
        "user_settings, x402_payments, paywall_items, paywall_purchases, "
        "paywall_channels, paywall_subscriptions RESTART IDENTITY"
    )
    ledger._conn.commit()


_ACTIVE_LEDGERS: list = []


def _pending_ledger(monkeypatch):
    from bot.ledger import Ledger

    fresh = Ledger(TEST_DB_URL)
    _reset_db(fresh)
    _ACTIVE_LEDGERS.append(fresh)
    monkeypatch.setattr(base, "ledger", fresh)
    return fresh


@pytest.fixture(autouse=True)
def _close_pending_ledgers():
    yield
    for led in _ACTIVE_LEDGERS:
        try:
            led.close()
        except Exception:
            pass
    _ACTIVE_LEDGERS.clear()


def _reserve_withdraw(ledger_obj, tg_id, amount_micro=5_000_000, tx_hash=None,
                      status="pending", age=0):
    """Simulate the handler: debit amount+fee, insert a pending withdraw row."""
    fee = base.withdraw_fee(amount_micro)
    ledger_obj._conn.execute(
        "INSERT INTO users (tg_id) VALUES (%s) ON CONFLICT (tg_id) DO NOTHING", (tg_id,)
    )
    ledger_obj._conn.execute(
        "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
        (amount_micro + fee, tg_id),
    )
    ledger_obj._conn.execute(
        "UPDATE users SET balance = balance - %s WHERE tg_id = %s",
        (amount_micro + fee, tg_id),
    )
    cur = ledger_obj._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash, status, created_at) "
        "VALUES ('withdraw', %s, %s, %s, %s, %s, %s) RETURNING id",
        (tg_id, "0x" + "a" * 40, amount_micro, tx_hash, status, int(time.time()) - age),
    )
    ledger_obj._conn.commit()
    return cur.fetchone()["id"]


def test_check_pending_withdraws_refunds_reverted(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    _reserve_withdraw(fresh, 777, tx_hash="0x" + "aa" * 32)

    class FakeEth:
        def get_transaction_receipt(self, tx):
            return {"status": 0}  # reverted

    monkeypatch.setattr(base, "w3", types.SimpleNamespace(eth=FakeEth()))
    asyncio.run(base.check_pending_withdraws())
    assert fresh.balance(777) == Decimal("5.050000")  # fully refunded
    row = fresh._conn.execute(
        "SELECT status FROM tx_log WHERE kind = 'withdraw'"
    ).fetchone()
    assert row["status"] == "refunded"


def test_check_pending_withdraws_marks_confirmed(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    _reserve_withdraw(fresh, 777, tx_hash="0x" + "ab" * 32)

    class FakeEth:
        def get_transaction_receipt(self, tx):
            return {"status": 1}  # mined ok

    monkeypatch.setattr(base, "w3", types.SimpleNamespace(eth=FakeEth()))
    asyncio.run(base.check_pending_withdraws())
    assert fresh.balance(777) == Decimal("0")  # stays debited
    row = fresh._conn.execute(
        "SELECT status FROM tx_log WHERE kind = 'withdraw'"
    ).fetchone()
    assert row["status"] == "done"


def test_check_pending_withdraws_stuck_gets_refunded(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    _reserve_withdraw(fresh, 777, tx_hash="0x" + "cd" * 32, age=3600)

    class FakeEth:
        def get_transaction_receipt(self, tx):
            return None  # still not mined

    monkeypatch.setattr(base, "w3", types.SimpleNamespace(eth=FakeEth()))
    asyncio.run(base.check_pending_withdraws())
    assert fresh.balance(777) == Decimal("5.050000")
    assert fresh._conn.execute(
        "SELECT status FROM tx_log WHERE kind = 'withdraw'"
    ).fetchone()["status"] == "refunded"


def test_check_pending_withdraws_recent_pending_kept(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    _reserve_withdraw(fresh, 777, tx_hash="0x" + "ef" * 32, age=10)

    class FakeEth:
        def get_transaction_receipt(self, tx):
            return None

    monkeypatch.setattr(base, "w3", types.SimpleNamespace(eth=FakeEth()))
    asyncio.run(base.check_pending_withdraws())
    assert fresh.balance(777) == Decimal("0")  # still pending, no refund yet


def test_check_pending_withdraws_crash_before_send_refunded(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    _reserve_withdraw(fresh, 777, tx_hash=None, age=3600)

    class FakeEth:
        def get_transaction_receipt(self, tx):
            raise AssertionError("no tx was ever sent")

    monkeypatch.setattr(base, "w3", types.SimpleNamespace(eth=FakeEth()))
    asyncio.run(base.check_pending_withdraws())
    assert fresh.balance(777) == Decimal("5.050000")  # crash leftover refunded
    assert fresh._conn.execute(
        "SELECT status FROM tx_log WHERE kind = 'withdraw'"
    ).fetchone()["status"] == "refunded"


def test_check_pending_withdraws_rpc_error_handled(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    _reserve_withdraw(fresh, 777, tx_hash="0x" + "90" * 32, age=3600)

    class FakeEth:
        def get_transaction_receipt(self, tx):
            raise ConnectionError("rpc down")

    monkeypatch.setattr(base, "w3", types.SimpleNamespace(eth=FakeEth()))
    asyncio.run(base.check_pending_withdraws())  # must not raise on RPC error
    # Old + RPC down -> treated as stuck, refunded.
    assert fresh.balance(777) == Decimal("5.050000")
    assert fresh._conn.execute(
        "SELECT status FROM tx_log WHERE kind = 'withdraw'"
    ).fetchone()["status"] == "refunded"


def test_check_pending_withdraws_legacy_marked_done(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    _reserve_withdraw(fresh, 777, tx_hash="0x" + "12" * 32, status=None, age=86400)

    class FakeEth:
        def get_transaction_receipt(self, tx):
            raise AssertionError("legacy rows must not hit RPC")

    monkeypatch.setattr(base, "w3", types.SimpleNamespace(eth=FakeEth()))
    asyncio.run(base.check_pending_withdraws())
    assert fresh._conn.execute(
        "SELECT status FROM tx_log WHERE kind = 'withdraw'"
    ).fetchone()["status"] == "done"


# ---------- channel paywall watcher ----------


class _KickBot:
    def __init__(self, members, banned=None, fail_get=False, ban_error=None, ban_error_msg=""):
        self.members = members  # tg_id -> status
        self.banned = banned or []
        self.sent = []
        self.fail_get = fail_get
        self.ban_error = ban_error  # exception class to raise on ban, or None
        self.ban_error_msg = ban_error_msg

    async def get_chat_member(self, chat_id, tg_id):
        if self.fail_get:
            raise ValueError("not found")
        return types.SimpleNamespace(status=self.members.get(tg_id, "left"))

    async def ban_chat_member(self, chat_id, tg_id):
        if self.ban_error is not None:
            if isinstance(self.ban_error_msg, tuple):
                raise self.ban_error(*self.ban_error_msg)
            raise self.ban_error(self.ban_error_msg)
        self.banned.append(tg_id)

    async def unban_chat_member(self, chat_id, tg_id):
        pass

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


def test_kick_expired_subscriptions(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    fresh.credit(777, 1_000_000, "deposit")
    fresh.ensure_user(888, "owner")
    fresh.set_paywall_channel(-100123, 888, 400_000)
    fresh.subscribe_channel(-100123, 777)
    # expire the subscription manually
    fresh._conn.execute(
        "UPDATE paywall_subscriptions SET expires_at = %s WHERE chat_id = -100123 AND tg_id = 777",
        (int(time.time()) - 10,),
    )
    fresh._conn.commit()
    bot = _KickBot(members={777: "member"})
    assert asyncio.run(base.kick_expired_channel_subscriptions(bot)) == 1
    assert bot.banned == [777]
    assert fresh.active_channel_subscriptions() == []
    assert any("истекла" in t for _, t in bot.sent)


def test_kick_skips_active_and_admins(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    fresh.credit(777, 1_000_000, "deposit")
    fresh.ensure_user(888, "owner")
    fresh.set_paywall_channel(-100123, 888, 400_000)
    fresh.subscribe_channel(-100123, 777)  # active — must be skipped
    fresh._conn.execute(
        "INSERT INTO paywall_subscriptions (chat_id, tg_id, expires_at) VALUES (%s, %s, %s)",
        (-100123, 999, int(time.time()) - 10),
    )
    fresh._conn.commit()
    bot = _KickBot(members={777: "member", 999: "creator"})
    assert asyncio.run(base.kick_expired_channel_subscriptions(bot)) == 0
    assert bot.banned == []
    # admin row dropped without a kick, member row kept
    assert fresh.channel_subscription(-100123, 777) is not None
    assert fresh.channel_subscription(-100123, 999) is None


def test_kick_drops_row_when_user_left(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    fresh.credit(777, 1_000_000, "deposit")
    fresh.ensure_user(888, "owner")
    fresh.set_paywall_channel(-100123, 888, 400_000)
    fresh.subscribe_channel(-100123, 777)
    fresh._conn.execute(
        "UPDATE paywall_subscriptions SET expires_at = %s WHERE chat_id = -100123 AND tg_id = 777",
        (int(time.time()) - 10,),
    )
    fresh._conn.commit()
    from aiogram.exceptions import TelegramBadRequest

    # user left the channel: the probe and the ban both fail with "not found"
    bot = _KickBot(
        members={}, fail_get=True,
        ban_error=TelegramBadRequest, ban_error_msg=("method", "user not found"),
    )
    assert asyncio.run(base.kick_expired_channel_subscriptions(bot)) == 0
    assert bot.banned == []
    assert fresh.active_channel_subscriptions() == []


def test_kick_keeps_row_when_bot_lost_admin(monkeypatch, tmp_path):
    """The row must survive a bot that lost admin rights: nobody stays in
    the channel for free forever — the kick retries once rights are back."""
    fresh = _pending_ledger(monkeypatch)
    fresh.credit(777, 1_000_000, "deposit")
    fresh.ensure_user(888, "owner")
    fresh.set_paywall_channel(-100123, 888, 400_000)
    fresh.subscribe_channel(-100123, 777)
    fresh._conn.execute(
        "UPDATE paywall_subscriptions SET expires_at = %s WHERE chat_id = -100123 AND tg_id = 777",
        (int(time.time()) - 10,),
    )
    fresh._conn.commit()
    from aiogram.exceptions import TelegramBadRequest

    bot = _KickBot(
        members={}, fail_get=True,
        ban_error=TelegramBadRequest, ban_error_msg="bot is not a member of the channel chat",
    )
    assert asyncio.run(base.kick_expired_channel_subscriptions(bot)) == 0
    assert bot.banned == []
    assert fresh.channel_subscription(-100123, 777) is not None  # still tracked


def test_kick_keeps_row_on_network_error(monkeypatch, tmp_path):
    fresh = _pending_ledger(monkeypatch)
    fresh.credit(777, 1_000_000, "deposit")
    fresh.ensure_user(888, "owner")
    fresh.set_paywall_channel(-100123, 888, 400_000)
    fresh.subscribe_channel(-100123, 777)
    fresh._conn.execute(
        "UPDATE paywall_subscriptions SET expires_at = %s WHERE chat_id = -100123 AND tg_id = 777",
        (int(time.time()) - 10,),
    )
    fresh._conn.commit()
    bot = _KickBot(members={777: "member"}, ban_error=ConnectionError, ban_error_msg="timeout")
    assert asyncio.run(base.kick_expired_channel_subscriptions(bot)) == 0
    assert bot.banned == []
    assert fresh.channel_subscription(-100123, 777) is not None
