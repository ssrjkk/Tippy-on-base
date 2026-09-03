"""Tests for the P2 Smart Wallet (ERC-4337) module.

Covers: config wiring, address prediction (mocked factory), balance query,
and the fallback behavior when smart wallet is not configured.
"""

import types

from eth_abi import decode as abi_decode
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


# ---------------------------------------------------------------------------
# Pure encoding / signing (no network)
# ---------------------------------------------------------------------------

def test_execute_selector_is_correct():
    # keccak("execute(address,uint256,bytes)")[:4] == 0xb61d27f6
    assert sw._ENCODE_EXECUTE_SELECTOR == "b61d27f6"


def test_encode_execute_computes_function_selector():
    dest = Web3.to_checksum_address("0x" + "ab" * 20)
    data = Web3.keccak(text="buy()")[:4]
    encoded = sw._encode_execute(dest, 0, data)
    assert encoded[:4].hex() == "b61d27f6", "execute selector mismatch"
    decoded = abi_decode(["address", "uint256", "bytes"], encoded[4:])
    assert decoded[0].lower() == dest.lower()
    assert decoded[1] == 0
    assert decoded[2] == data


def test_encode_execute_batch_selector_is_correct():
    assert sw._ENCODE_EXECUTE_BATCH_SELECTOR == "7c7652c8"


def test_encode_execute_batch_decodes_destinations():
    usdc = Web3.to_checksum_address("0x" + "cd" * 20)
    market = Web3.to_checksum_address("0x" + "ef" * 20)
    approve = Web3.keccak(text="approve()")[:4]
    trade = Web3.keccak(text="buy()")[:4]
    encoded = sw._encode_execute_batch(usdc, approve, market, trade)
    assert encoded[:4].hex() == "7c7652c8", "executeBatch selector mismatch"
    args = abi_decode(
        ["address", "bytes", "address", "bytes"], encoded[4:]
    )
    assert args[0].lower() == usdc.lower()
    assert args[1] == approve
    assert args[2].lower() == market.lower()
    assert args[3] == trade


def test_pack_user_op_roundtrip():
    op = {
        "sender": "0x" + "11" * 20,
        "nonce": 7,
        "initCode": b"",
        "callData": b"\xab" * 4,
        "callGasLimit": 100,
        "verificationGasLimit": 200,
        "preVerificationGas": 300,
        "maxFeePerGas": 4,
        "maxPriorityFeePerGas": 5,
        "paymasterAndData": b"\x00" * 20,
        "signature": b"\x99" * 65,
    }
    packed = sw._pack_user_op(op)
    assert packed == (
        op["sender"], op["nonce"], op["initCode"], op["callData"],
        op["callGasLimit"], op["verificationGasLimit"],
        op["preVerificationGas"], op["maxFeePerGas"],
        op["maxPriorityFeePerGas"], op["paymasterAndData"],
        op["signature"],
    )


def test_build_paymaster_data_layout():
    pm = "0x" + "22" * 20
    tg_id = 123456789
    sig = b"\x01" * 65
    sw.config.SMART_WALLET_PAYMASTER_ADDRESS = pm
    data = sw._build_paymaster_data(tg_id, sig)
    # 20 (paymaster) + 32 (tg_id) + 65 (sig) = 117 bytes
    assert len(data) == 20 + 32 + 65
    assert data[:20] == bytes.fromhex(pm[2:])
    assert int.from_bytes(data[20:52], "big") == tg_id
    assert data[52:] == sig


def test_eth_account_signing_primitive():
    from eth_account import Account
    from eth_account.messages import encode_defunct
    key = "0x" + "ab" * 32
    acct = Account.from_key(key)
    msg = Web3.keccak(text="chainid bullshark") + b"!" * 4
    # Same signing/verification the UserOp + paymaster flow relies on:
    # Account.sign_message(encode_defunct(hash)) uses the EIP-191 personal-message
    # prefix, matching SmartAccount.validateUserOp's ecrecover over the ring
    # keccak256("\x19Ethereum Signed Message:\n32" || hash).
    signed = Account.sign_message(encode_defunct(primitive=msg), key)
    recovered = Account.recover_message(encode_defunct(primitive=msg), signature=signed.signature)
    assert recovered.lower() == acct.address.lower()


def test_sign_paymaster_is_eip191(monkeypatch):
    # _sign_paymaster must produce a recoverable signature without any network.
    from eth_account import Account
    from eth_account.messages import encode_defunct
    key = "0x" + "cd" * 32
    acct = Account.from_key(key)
    user_op_hash = Web3.keccak(text="userop hash")[:32]
    sig = sw._sign_paymaster(user_op_hash, 123, key)
    assert len(sig) == 65
    recovered = Account.recover_message(
        encode_defunct(primitive=user_op_hash), signature=sig
    )
    assert recovered.lower() == acct.address.lower()


def test_sign_user_op_uses_mocked_entrypoint_hash(monkeypatch):
    # The entrypoint stub returns b"\x00"*32 for getUserOpHash; _sign_user_op
    # must sign that hash and return a 65-byte EIP-191 signature.
    from eth_account import Account
    from eth_account.messages import encode_defunct
    _monkeypatch_smart_wallet(monkeypatch)
    key = "0x" + "ef" * 32
    acct = Account.from_key(key)
    op = {
        "sender": "0x" + "11" * 20,
        "nonce": 0,
        "initCode": b"",
        "callData": b"\xab" * 4,
        "callGasLimit": 1,
        "verificationGasLimit": 2,
        "preVerificationGas": 3,
        "maxFeePerGas": 4,
        "maxPriorityFeePerGas": 5,
        "paymasterAndData": b"",
        "signature": b"",
    }
    sig = sw._sign_user_op(op, key)
    assert len(sig) == 65
    recovered = Account.recover_message(
        encode_defunct(primitive=b"\x00" * 32), signature=sig
    )
    assert recovered.lower() == acct.address.lower()
