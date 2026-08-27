"""Tests for the extended onchain toolkit in bot/base.py (mocked RPC).

Covers: chain guard, nonce, contract detection, blocks, EIP-1559 fees,
tx lifecycle, generic ERC-20 send/approve, Chainlink feeds, Basenames.
Signing crypto is real; no network access anywhere.
"""

import asyncio
import time
import types

import pytest
from web3 import Web3

from bot import base, config


def _fake_w3(monkeypatch, *, chain_id=8453, base_fee=1_000_000_000, tx_count=5):
    """Replace base.w3 with a controllable stub. Returns the fake."""
    eth = types.SimpleNamespace(
        account=base.w3.eth.account,
        block_number=1_000,
        chain_id=chain_id,
        get_transaction_count=lambda a, p="latest": tx_count,
        get_block=lambda x: {
            "number": 1_000,
            "hash": b"\x01" * 32,
            "timestamp": 1_700_000_000,
            "transactions": [b"", b""],
            "baseFeePerGas": base_fee,
        },
        get_code=lambda a: b"",
        get_transaction_receipt=lambda h: (_ for _ in ()).throw(Exception("no receipt")),
        get_transaction=lambda h: (_ for _ in ()).throw(Exception("no tx")),
    )
    fake = types.SimpleNamespace(eth=eth, to_wei=base.w3.to_wei, keccak=Web3.keccak)
    monkeypatch.setattr(base.core, "w3", fake)
    # keep the failover list pointed at the stub too, so _contract_read
    # never reaches for the real network
    monkeypatch.setattr(base.core, "_w3_providers", [fake])
    return fake


def _bind_contract(fake, contract):
    """Route every w3.eth.contract(...) call to `contract`."""
    fake.eth.contract = lambda address=None, abi=None: contract


def _stub_signer(fake):
    """Replace the signing account with a deterministic stub."""
    signer = types.SimpleNamespace(
        sign_transaction=lambda tx: types.SimpleNamespace(raw_transaction=b"\x07" * 32)
    )
    holder = types.SimpleNamespace(from_key=lambda k: signer)
    fake.eth.account = holder
    return signer


# ---------------------------------------------------------------- namehash

def test_namehash_empty_is_zero():
    assert base.namehash("") == b"\x00" * 32
    assert base.namehash(None) == b"\x00" * 32


def test_namehash_known_vector_eth():
    # Canonical ENSIP-1 vector: namehash('eth')
    expected = "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    assert base.namehash("eth").hex() == expected


def test_namehash_deterministic_and_label_order_sensitive():
    a = base.namehash("myname.base.eth")
    assert a == base.namehash("myname.base.eth.")
    assert a != base.namehash("other.base.eth")


# ------------------------------------------------------------ chain guard

def test_assert_base_chain_passes_on_expected(monkeypatch):
    _fake_w3(monkeypatch, chain_id=8453)
    assert base.assert_base_chain_sync() == 8453


def test_assert_base_chain_rejects_wrong_chain(monkeypatch):
    _fake_w3(monkeypatch, chain_id=1)  # mainnet by accident
    with pytest.raises(RuntimeError, match="refusing to sign"):
        base.assert_base_chain_sync()


def test_assert_base_chain_skipped_when_disabled(monkeypatch):
    _fake_w3(monkeypatch, chain_id=999)
    monkeypatch.setattr(config, "EXPECTED_CHAIN_ID", 0)
    assert base.assert_base_chain_sync() == 999


# ------------------------------------------------------- basic read calls

def test_nonce_reads_pending_counter(monkeypatch):
    seen = {}

    def gtc(addr, param):
        seen["addr"], seen["param"] = addr, param
        return 7

    fake = _fake_w3(monkeypatch, tx_count=7)
    fake.eth.get_transaction_count = gtc
    assert base.nonce_sync(base.hot_wallet()) == 7
    assert seen["param"] == "pending"


def test_is_contract_false_for_eoa(monkeypatch):
    _fake_w3(monkeypatch)
    assert base.is_contract_sync(base.hot_wallet()) is False


def test_is_contract_true_for_contract(monkeypatch):
    fake = _fake_w3(monkeypatch)
    fake.eth.get_code = lambda a: b"\x60\x80\x60\x40"
    assert base.is_contract_sync(base.hot_wallet()) is True


def test_get_block_mapping(monkeypatch):
    _fake_w3(monkeypatch, base_fee=2_500_000_000)
    b = base.get_block_sync()
    assert b["number"] == 1_000
    assert b["timestamp"] == 1_700_000_000
    assert b["transactions"] == 2
    assert abs(b["base_fee_gwei"] - 2.5) < 1e-9


def test_get_block_missing_returns_none(monkeypatch):
    fake = _fake_w3(monkeypatch)
    fake.eth.get_block = lambda x: (_ for _ in ()).throw(Exception("gone"))
    assert base.get_block_sync(123) is None


# ------------------------------------------------------------------ fees

def test_eip1559_fees_math(monkeypatch):
    _fake_w3(monkeypatch, base_fee=1_000_000_000)  # 1 gwei
    fees = base.eip1559_fees_sync(priority_gwei=0.01)
    assert fees["base_fee_gwei"] == pytest.approx(1.0)
    assert fees["priority_gwei"] == pytest.approx(0.01)
    assert fees["max_fee_gwei"] == pytest.approx(2 * 1.0 + 0.01)


def test_eip1559_fees_async_wrapper(monkeypatch):
    _fake_w3(monkeypatch)

    async def check():
        fees = await base.eip1559_fees()
        assert {"base_fee_gwei", "priority_gwei", "max_fee_gwei"} <= set(fees)

    asyncio.run(check())


# --------------------------------------------------------- tx lifecycle

def test_wait_for_tx_immediate_receipt(monkeypatch):
    fake = _fake_w3(monkeypatch)
    receipt = {"status": 1, "blockNumber": 1234, "gasUsed": 45000, "effectiveGasPrice": 50_000_000}
    fake.eth.get_transaction_receipt = lambda h: receipt
    out = base.wait_for_tx_sync("0x" + "ab" * 32, timeout=1)
    assert out == {
        "status": True,
        "block_number": 1234,
        "gas_used": 45_000,
        "effective_gas_price_gwei": 0.05,
    }


def test_wait_for_tx_times_out(monkeypatch):
    _fake_w3(monkeypatch)  # receipt always raises
    start = time.monotonic()
    out = base.wait_for_tx_sync("0x" + "ab" * 32, timeout=0.05, poll=0.02)
    assert out is None
    assert time.monotonic() - start < 2


def test_tx_status_unknown(monkeypatch):
    _fake_w3(monkeypatch)
    assert asyncio.run(base.tx_status("0x" + "aa" * 32)) == "unknown"


def test_tx_status_success_failed_pending(monkeypatch):
    fake = _fake_w3(monkeypatch)
    states = {}

    def receipt(h):
        if states[h] in ("missing", "pending"):
            raise Exception("not mined")
        return {"status": states[h]}

    def txn(h):
        if states[h] == "pending":
            return {"hash": h}
        raise Exception("unknown tx")

    fake.eth.get_transaction_receipt = receipt
    fake.eth.get_transaction = txn

    async def check():
        states["h1"] = 1
        assert await base.tx_status("h1") == "success"
        states["h2"] = 0
        assert await base.tx_status("h2") == "failed"
        states["h3"] = "pending"
        assert await base.tx_status("h3") == "pending"

    asyncio.run(check())


# --------------------------------------------- generic ERC-20 send/approve

class _Capture:
    def __init__(self):
        self.data = {}


def _erc20_fake(fake, capture, fn_name, args_names=("to", "amount")):
    class Fn:
        def __getattr__(self, name):
            if name != fn_name:
                raise AttributeError(name)

            def call(*a):
                for k, v in zip(args_names, a):
                    capture.data[k] = v
                return SelfBuilder()

            return call

    class SelfBuilder:
        def build_transaction(self, kw):
            capture.data.update(kw)
            return dict(kw, gas=60_000)

    _bind_contract(fake, types.SimpleNamespace(functions=Fn()))


def test_send_token_builds_generic_transfer(monkeypatch):
    fake = _fake_w3(monkeypatch, tx_count=5)
    cap = _Capture()
    _erc20_fake(fake, cap, "transfer")

    class Fn:
        pass

    _stub_signer(fake)
    sent = {}
    fake.eth.send_raw_transaction = lambda raw: sent.setdefault("raw", raw)

    token_addr = Web3.to_checksum_address("0x" + "77" * 20)
    tx_hash = base._send_token_sync("0x" + "44" * 20, 5_000_000, token_addr)

    assert tx_hash.startswith("0x") and len(tx_hash) == 66
    assert cap.data["to"] == Web3.to_checksum_address("0x" + "44" * 20)
    assert cap.data["amount"] == 5_000_000
    assert cap.data["nonce"] == 5
    assert cap.data["chainId"] == 8453
    assert sent["raw"] == b"\x07" * 32


def test_send_usdc_uses_prebound_handle(monkeypatch):
    """token_address=None must go through the pre-bound `usdc` handle."""
    fake = _fake_w3(monkeypatch)
    cap = _Capture()

    class Fn:
        def transfer(self, to, amount):
            cap.data["to"], cap.data["amount"] = to, amount
            return self

        def build_transaction(self, kw):
            cap.data.update(kw)
            return dict(kw, gas=60_000)

    monkeypatch.setattr(base.core, "usdc", types.SimpleNamespace(functions=Fn()))
    _stub_signer(fake)
    fake.eth.send_raw_transaction = lambda raw: b"\x01"

    def no_rebind(address=None, abi=None):
        raise AssertionError("must not rebind USDC via eth.contract")

    fake.eth.contract = no_rebind
    base._send_token_sync("0x" + "44" * 20, 100, None)
    assert cap.data["amount"] == 100


def test_approve_token_builds_approval(monkeypatch):
    fake = _fake_w3(monkeypatch)
    cap = _Capture()

    class Fn:
        def approve(self, spender, amount):
            cap.data["spender"], cap.data["amount"] = spender, amount
            return self

        def build_transaction(self, kw):
            cap.data.update(kw)
            return dict(kw, gas=50_000)

    _bind_contract(fake, types.SimpleNamespace(functions=Fn()))
    _stub_signer(fake)
    fake.eth.send_raw_transaction = lambda raw: b"\x02"

    tx_hash = base._approve_token_sync("0x" + "55" * 20, 100, "0x" + "66" * 20)
    assert tx_hash.startswith("0x")
    assert cap.data["spender"] == Web3.to_checksum_address("0x" + "55" * 20)
    assert cap.data["amount"] == 100
    assert cap.data["nonce"] >= 0


def test_send_path_refuses_when_chain_guard_fails(monkeypatch):
    fake = _fake_w3(monkeypatch, chain_id=137)  # polygon by accident
    boom = []

    def no_send(raw):
        boom.append(raw)
        raise AssertionError("send must not happen on the wrong chain")

    fake.eth.send_raw_transaction = no_send
    with pytest.raises(RuntimeError, match="refusing to sign"):
        base._send_eth_sync("0x" + "33" * 20, 10**15)
    assert not boom


# ---------------------------------------------------------- price feeds

def _feed_contract(answer, updated_at, decimals=8):
    class Feed:
        class functions:
            @staticmethod
            def latestRoundData():
                class C:
                    @staticmethod
                    def call():
                        return (1, answer, 0, updated_at, 1)

                return C()

            @staticmethod
            def decimals():
                class C:
                    @staticmethod
                    def call():
                        return decimals

                return C()

    return Feed()


def test_feed_price_fresh_answer(monkeypatch):
    fake = _fake_w3(monkeypatch)
    feed = _feed_contract(int(3000.12345678 * 1e8), int(time.time()))
    _bind_contract(fake, feed)
    assert base.feed_price_sync("0x" + "88" * 20) == pytest.approx(3000.12345678)


def test_feed_price_stale_answer_rejected(monkeypatch):
    fake = _fake_w3(monkeypatch)
    stale_ts = int(time.time()) - config.PRICE_FEED_MAX_AGE_SECONDS - 100
    _bind_contract(fake, _feed_contract(int(3000 * 1e8), stale_ts))
    assert base.feed_price_sync("0x" + "88" * 20) is None


def test_feed_price_negative_answer_rejected(monkeypatch):
    fake = _fake_w3(monkeypatch)
    _bind_contract(fake, _feed_contract(-5, int(time.time())))
    assert base.feed_price_sync("0x" + "88" * 20) is None


def test_feed_price_rpc_failure_returns_none(monkeypatch):
    fake = _fake_w3(monkeypatch)

    class Broken:
        class functions:
            @staticmethod
            def latestRoundData():
                raise Exception("rpc down")

    _bind_contract(fake, Broken())
    assert base.feed_price_sync("0x" + "88" * 20) is None


def test_usdc_and_eth_price_wrappers_use_config_feeds(monkeypatch):
    _fake_w3(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        base, "feed_price_sync", lambda addr, **kw: seen.setdefault(addr, kw.get("max_age_seconds"))
    )
    # ETH uses the default window (no explicit override -> None marker)
    assert base.get_eth_price_usd_sync() is None
    assert config.CHAINLINK_ETH_USD_FEED in seen
    assert seen[config.CHAINLINK_ETH_USD_FEED] is None
    # USDC gets its own wider heartbeat-aware window
    assert base.get_usdc_price_usd_sync() == config.USDC_PRICE_FEED_MAX_AGE_SECONDS
    assert config.CHAINLINK_USDC_USD_FEED in seen


# ------------------------------------------------------- L2 sequencer guard

def test_l2_sequencer_healthy(monkeypatch):
    fake = _fake_w3(monkeypatch)
    _bind_contract(fake, _feed_contract(0, int(time.time()) - 999999))
    assert base.l2_sequencer_ok_sync() is True


def test_l2_sequencer_outage_detected(monkeypatch):
    fake = _fake_w3(monkeypatch)
    _bind_contract(fake, _feed_contract(int(120 * 1e8), int(time.time())))
    assert base.l2_sequencer_ok_sync() is False


def test_l2_sequencer_unavailable_is_unknown(monkeypatch):
    fake = _fake_w3(monkeypatch)

    class Broken:
        class functions:
            @staticmethod
            def latestRoundData():
                raise Exception("rpc down")

    _bind_contract(fake, Broken())
    assert base.l2_sequencer_ok_sync() is None


def test_l2_sequencer_no_feed_configured(monkeypatch):
    _fake_w3(monkeypatch)
    monkeypatch.setattr(config, "CHAINLINK_L2_SEQUENCER_FEED", "")
    assert base.l2_sequencer_ok_sync() is None


# -------------------------------------------------------------- ERC20 meta

class _MetaFn:
    @staticmethod
    def symbol():
        class C:
            @staticmethod
            def call():
                return "TT"

        return C()

    @staticmethod
    def decimals():
        class C:
            @staticmethod
            def call():
                return 18

        return C()

    @staticmethod
    def name():
        class C:
            @staticmethod
            def call():
                return "TestToken"

        return C()


def test_token_meta_reads_and_caches(monkeypatch):
    fake = _fake_w3(monkeypatch)
    _bind_contract(fake, types.SimpleNamespace(functions=_MetaFn()))
    base._token_meta_cache.clear()
    tok = Web3.to_checksum_address("0x" + "99" * 20)
    meta = base.token_meta_sync(tok)
    assert meta == {"address": tok, "symbol": "TT", "decimals": 18, "name": "TestToken"}
    meta["symbol"] = "MUTATED"
    assert base.token_meta_sync(tok)["symbol"] == "TT"  # cache returns copies


def test_token_meta_survives_missing_name(monkeypatch):
    fake = _fake_w3(monkeypatch)

    class NoName:
        @staticmethod
        def symbol():
            class C:
                @staticmethod
                def call():
                    return "MN"

            return C()

        @staticmethod
        def decimals():
            class C:
                @staticmethod
                def call():
                    return 6

            return C()

        @staticmethod
        def name():
            raise Exception("no name()")

    _bind_contract(fake, types.SimpleNamespace(functions=NoName()))
    base._token_meta_cache.clear()
    meta = base.token_meta_sync(Web3.to_checksum_address("0x" + "aa" * 20))
    assert meta["symbol"] == "MN" and meta["name"] == ""


# --------------------------------------------------------------- basenames

def test_resolve_basename_rejects_bad_names_without_rpc(monkeypatch):
    _fake_w3(monkeypatch)
    bad_names = [
        "",
        "no-tld",
        "a.b.eth",
        "-bad.base.eth",
        f"{'x' * 70}.base.eth",
        "ok.base.com",
        "spaces .base.eth",
    ]
    for bad in bad_names:
        assert base.resolve_basename_sync(bad) is None, bad


def test_resolve_basename_happy_path(monkeypatch):
    fake = _fake_w3(monkeypatch)

    class Resolver:
        class functions:
            @staticmethod
            def addr(node):
                assert isinstance(node, bytes) and len(node) == 32

                class C:
                    @staticmethod
                    def call():
                        return "0x" + "42" * 20

                return C()

    _bind_contract(fake, Resolver())
    out = base.resolve_basename_sync("MyName.Base.ETH")
    assert out == Web3.to_checksum_address("0x" + "42" * 20)


def test_resolve_basename_unregistered_zero_addr(monkeypatch):
    fake = _fake_w3(monkeypatch)

    class Resolver:
        class functions:
            @staticmethod
            def addr(node):
                class C:
                    @staticmethod
                    def call():
                        return "0x" + "00" * 20  # zero address = unregistered

                return C()

    _bind_contract(fake, Resolver())
    assert base.resolve_basename_sync("free.base.eth") is None


def test_reverse_basename_returns_name_or_none(monkeypatch):
    fake = _fake_w3(monkeypatch)

    seen_nodes = []

    class RegistrarAndResolver:
        class functions:
            @staticmethod
            def node(addr_cs):
                assert addr_cs == Web3.to_checksum_address("0x" + "11" * 20)

                class N:
                    @staticmethod
                    def call():
                        return b"\xab" * 32

                return N()

            @staticmethod
            def name(node):
                seen_nodes.append(node)

                class C:
                    @staticmethod
                    def call():
                        return "vitalik.base.eth"

                return C()

    _bind_contract(fake, RegistrarAndResolver())
    assert base.reverse_basename_sync("0x" + "11" * 20) == "vitalik.base.eth"
    # the node handed to name() must be the one the registrar returned
    assert seen_nodes == [b"\xab" * 32]

    class Broken:
        class functions:
            @staticmethod
            def node(addr_cs):
                raise Exception("registrar down")

    _bind_contract(fake, Broken())
    assert base.reverse_basename_sync("0x" + "11" * 20) is None


# ------------------------------------------------- signing-path integration

def test_send_eth_still_works_after_refactor(monkeypatch):
    """Regression: the shared _build_and_send path keeps send_eth intact."""
    fake = _fake_w3(monkeypatch, tx_count=3, base_fee=500_000_000)
    sent = {}

    def send_raw(raw):
        sent["raw"] = raw
        return b"\x04" * 32

    fake.eth.send_raw_transaction = send_raw
    tx_hash = base._send_eth_sync("0x" + "33" * 20, 10**15)
    assert tx_hash.startswith("0x") and len(tx_hash) == 66


# ------------------------------------------------------------ price caching

def test_price_cache_hits_within_ttl(monkeypatch):
    calls = {"n": 0}

    def fake_feed_read(addr, *, decimals=None):
        calls["n"] += 1
        return 3000_00000000, int(time.time()), 8

    monkeypatch.setattr(base.prices, "_feed_read", fake_feed_read)
    monkeypatch.setattr(config, "PRICE_CACHE_SECONDS", 60)
    base.price_cache_clear()
    feed = "0x" + "aa" * 20
    assert base.feed_price_sync(feed) == 3000.0
    assert base.feed_price_sync(feed) == 3000.0  # served from cache
    assert calls["n"] == 1
    base.feed_price_sync("0x" + "bb" * 20)  # different key -> fresh fetch
    assert calls["n"] == 2


def test_price_cache_disabled_reads_fresh(monkeypatch):
    calls = {"n": 0}

    def fake_feed_read(addr, *, decimals=None):
        calls["n"] += 1
        return 2500_00000000, int(time.time()), 8

    monkeypatch.setattr(base.prices, "_feed_read", fake_feed_read)
    monkeypatch.setattr(config, "PRICE_CACHE_SECONDS", 0)
    base.price_cache_clear()
    feed = "0x" + "cc" * 20
    base.feed_price_sync(feed)
    base.feed_price_sync(feed)
    assert calls["n"] == 2


def test_price_cache_does_not_pin_failures(monkeypatch):
    """A failed read must not poison the cache: the next call retries."""
    state = {"ok": False}

    def flaky_feed_read(addr, *, decimals=None):
        if not state["ok"]:
            raise RuntimeError("all RPC providers failed")
        return 100 * 10**8, int(time.time()), 8

    monkeypatch.setattr(base.prices, "_feed_read", flaky_feed_read)
    monkeypatch.setattr(config, "PRICE_CACHE_SECONDS", 60)
    base.price_cache_clear()
    feed = "0x" + "dd" * 20
    assert base.feed_price_sync(feed) is None
    state["ok"] = True
    assert base.feed_price_sync(feed) == 100.0


# ------------------------------------------------------- Aerodrome DEX quote

def test_aerodrome_quote_success(monkeypatch):
    captured = {}

    def fake_read(addr, abi, fn_name, amount_in, routes):
        captured.update(addr=addr, fn=fn_name, amount=amount_in, route=routes)
        return [amount_in, 4_000_000_000_000_000_000]

    monkeypatch.setattr(base.core, "_contract_read", fake_read)
    out = base.aerodrome_quote_sync(10_000_000, config.USDC_ADDRESS, config.WETH_ADDRESS)
    assert out == 4_000_000_000_000_000_000
    assert captured["fn"] == "getAmountsOut"
    assert captured["amount"] == 10_000_000
    route = captured["route"][0]
    assert route[0] == Web3.to_checksum_address(config.USDC_ADDRESS)
    assert route[1] == Web3.to_checksum_address(config.WETH_ADDRESS)
    assert route[2] is False
    assert route[3] == Web3.to_checksum_address(config.AERODROME_FACTORY_ADDRESS)
    # convenience wrapper converts wei -> ETH float
    assert base.usdc_to_eth_quote_sync(10_000_000) == 4.0


def test_aerodrome_quote_stable_pool_flag(monkeypatch):
    seen = {}

    def fake_read(addr, abi, fn_name, amount_in, routes):
        seen["stable"] = routes[0][2]
        return [amount_in, 1]

    monkeypatch.setattr(base.core, "_contract_read", fake_read)
    base.aerodrome_quote_sync(1, config.USDC_ADDRESS, config.USDC_ADDRESS, stable=True)
    assert seen["stable"] is True


def test_aerodrome_quote_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("all RPC providers failed")

    monkeypatch.setattr(base.core, "_contract_read", boom)
    assert base.aerodrome_quote_sync(1, config.USDC_ADDRESS, config.WETH_ADDRESS) is None
    assert base.usdc_to_eth_quote_sync(1) is None


# ------------------------------------------------------ basename availability

def test_basename_available_free_and_taken(monkeypatch):
    fake = _fake_w3(monkeypatch)
    owners = {}

    class Registry:
        class functions:
            @staticmethod
            def owner(node):
                class C:
                    @staticmethod
                    def call():
                        return owners.get(node.hex(), "0x" + "00" * 20)

                return C()

    _bind_contract(fake, Registry())
    free = base.namehash("free.base.eth")
    taken = base.namehash("jesse.base.eth")
    owners[free.hex()] = "0x" + "00" * 20
    owners[taken.hex()] = Web3.to_checksum_address("0x" + "77" * 20)
    assert base.basename_available_sync("free.base.eth") is True
    assert base.basename_available_sync("jesse.base.eth") is False


def test_basename_available_bad_name_and_errors(monkeypatch):
    _fake_w3(monkeypatch)
    assert base.basename_available_sync("not-a-basename") is None
    assert base.basename_available_sync("") is None

    def boom(*a, **k):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(base.core, "_contract_read", boom)
    assert base.basename_available_sync("whatever.base.eth") is None


def test_is_basename_helper():
    assert base.is_basename("jesse.base.eth")
    assert base.is_basename("My-Wallet.Base.ETH")
    assert not base.is_basename("vitalik.eth")
    assert not base.is_basename("@nick")
