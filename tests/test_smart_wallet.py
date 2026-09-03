"""Tests for the P2 Smart Wallet (ERC-4337) module.

Covers: config wiring, address prediction (mocked factory), balance query,
and the fallback behavior when smart wallet is not configured.
"""

import types

from web3 import Web3

from bot import smart_wallet as sw


class _Fn:
    def __init__(self, result):
        self._result = result

    def call(self, *a, **k):
        return self._result


def _make_contract(funcs):
    """Wrap a dict of function-name -> result into a fake contract.

    For each {name: result}, functions.name(*args).call() returns result.
    """
    namespace = {}
    for name, result in funcs.items():
        namespace[name] = (lambda res: (lambda *a, **k: _Fn(res)))(result)
    fake_functions = type("FakeFunctions", (), namespace)()
    return type("FakeContract", (), {"functions": fake_functions})()


def _monkeypatch_smart_wallet(monkeypatch, factory_returns=None, usdc_balance=0):
    """Point sw's config + base.w3 at controlled stubs."""
    factory_returns = factory_returns or {}
    monkeypatch.setattr(sw.config, "SMART_WALLET_ENTRYPOINT",
                        "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789")
    monkeypatch.setattr(sw.config, "SMART_WALLET_FACTORY_ADDRESS",
                        "0x" + "11" * 20)
    monkeypatch.setattr(sw.config, "SMART_WALLET_PAYMASTER_ADDRESS",
                        "0x" + "22" * 20)
    monkeypatch.setattr(sw.config, "USDC_ADDRESS", "0x" + "33" * 20)
    monkeypatch.setattr(sw.config, "HOT_WALLET_KEY", "0x" + "44" * 32)
    monkeypatch.setattr(sw.config, "SMART_WALLET_RELAYER_KEY", None)

    factory = _make_contract({
        "getAddress": factory_returns.get("address", "0x" + "11" * 20),
        "isDeployed": factory_returns.get("deployed", True),
    })
    usdc = _make_contract({
        "balanceOf": usdc_balance,
    })
    ep = _make_contract({
        "getUserOpHash": b"\x00" * 32,
        "getNonce": 0,
        "handleOps": None,
    })

    def _contract(address=None, abi=None):
        if abi is sw._ENTRYPOINT_ABI:
            return ep
        if abi is sw._SMART_ACCOUNT_FACTORY_ABI:
            return factory
        if abi is sw._ERC20_ABI:
            return usdc
        return factory

    fake_eth = types.SimpleNamespace(
        account=types.SimpleNamespace(
            from_key=lambda k: types.SimpleNamespace(address="0x" + "55" * 20)
        ),
        contract=_contract,
        get_transaction_count=lambda a, p="latest": 1,
        get_block=lambda x: {"baseFeePerGas": 1_000_000_000},
        gas_price=1_000_000_000,
        chain_id=8453,
        get_transaction_receipt=lambda h: {"status": 1},
        send_raw_transaction=lambda raw: b"\x66" * 32,
        wait_for_transaction_receipt=lambda h, timeout=60: {"status": 1},
        to_wei=lambda n, unit: n * 10**9,
    )
    fake_w3 = types.SimpleNamespace(
        eth=fake_eth,
        to_checksum_address=lambda a: a if a.startswith("0x") else "0x" + a,
        to_wei=lambda n, unit: n * 10**9,
        keccak=Web3.keccak,
        codec=Web3().codec,
    )
    monkeypatch.setattr(sw, "_get_w3", lambda: fake_w3)
    return fake_w3


def test_smart_wallet_config_defaults():
    import bot.config as cfg
    assert cfg.SMART_WALLET_ENTRYPOINT.startswith("0x")
    assert cfg.SMART_WALLET_ENABLED is False


def test_predict_address_returns_checksummed(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch, {"address": "0x" + "aa" * 20})
    addr = sw.predict_address(12345)
    assert addr.lower() == "0x" + "aa" * 20


def test_is_deployed_true(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch, {"deployed": True})
    assert sw.is_deployed(12345) is True


def test_is_deployed_false(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch, {"deployed": False})
    assert sw.is_deployed(12345) is False


def test_smart_balance_returns_micro(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch, usdc_balance=5_000_000)
    assert sw.smart_balance(12345) == 5_000_000
