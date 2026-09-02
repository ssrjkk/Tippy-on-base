"""Tests for CREATE2 per-user deposit addresses (bot/create2.py).

Covers: deterministic address derivation, input sensitivity, feature toggle,
and bytecode completeness check. All crypto is real; no network access.
"""

from web3 import Web3

from bot import create2

# ------------------------------------------------------------------ helpers

FACTORY = "0x" + "ab" * 20
COMPLETE_BYTECODE = (
    "0x3d602d80600a3d3981f3363d3d373d3d3d363d73"
    + "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    + "5af43d82803e903d91602b57fd5bf3"
)  # 45 bytes — a valid EIP-1167 minimal proxy prefix+impl+suffix

# Explicitly incomplete (prefix-only): 20 bytes, not a deployable proxy.
PREFIX_ONLY_BYTECODE = "0x3d602d80600a3d3981f3363d3d373d3d3d363d73"


# --------------------------------------------------- test_bytecode_complete


def test_bytecode_complete_prefix_only_is_incomplete(monkeypatch):
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", PREFIX_ONLY_BYTECODE)
    assert create2._bytecode_complete() is False


def test_bytecode_complete_full_proxy(monkeypatch):
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", COMPLETE_BYTECODE)
    assert create2._bytecode_complete() is True


def test_bytecode_complete_invalid_hex(monkeypatch):
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", "0xZZZZ")
    assert create2._bytecode_complete() is False


def test_bytecode_exact_45_bytes(monkeypatch):
    hex_str = "aa" * 45
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", "0x" + hex_str)
    assert create2._bytecode_complete() is True


def test_bytecode_44_bytes_is_incomplete(monkeypatch):
    hex_str = "aa" * 44
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", "0x" + hex_str)
    assert create2._bytecode_complete() is False


# --------------------------------------------------- test_is_create2_enabled


def _enable_create2(monkeypatch):
    """Set all three prerequisites so CREATE2 is enabled."""
    monkeypatch.setattr(create2, "FACTORY_ADDRESS", FACTORY)
    monkeypatch.setattr(create2, "FORWARDER_ADDRESS", "0x" + "cd" * 20)
    monkeypatch.setattr(create2, "CREATE2_SAFE_DEPOSITS", True)
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", COMPLETE_BYTECODE)


def test_is_create2_enabled_when_all_set(monkeypatch):
    _enable_create2(monkeypatch)
    assert create2.is_create2_enabled() is True


def test_is_create2_disabled_no_factory(monkeypatch):
    monkeypatch.setattr(create2, "FACTORY_ADDRESS", None)
    monkeypatch.setattr(create2, "FORWARDER_ADDRESS", "0x" + "cd" * 20)
    monkeypatch.setattr(create2, "CREATE2_SAFE_DEPOSITS", True)
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", COMPLETE_BYTECODE)
    assert create2.is_create2_enabled() is False


def test_is_create2_disabled_no_safe_opt_in(monkeypatch):
    monkeypatch.setattr(create2, "FACTORY_ADDRESS", FACTORY)
    monkeypatch.setattr(create2, "CREATE2_SAFE_DEPOSITS", False)
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", COMPLETE_BYTECODE)
    assert create2.is_create2_enabled() is False


def test_is_create2_disabled_bytecode_incomplete(monkeypatch):
    monkeypatch.setattr(create2, "FACTORY_ADDRESS", FACTORY)
    monkeypatch.setattr(create2, "CREATE2_SAFE_DEPOSITS", True)
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", PREFIX_ONLY_BYTECODE)
    assert create2.is_create2_enabled() is False


# --------------------------------------------------- test_compute_address_*


def test_compute_address_deterministic(monkeypatch):
    _enable_create2(monkeypatch)
    a1 = create2._compute_address(12345)
    a2 = create2._compute_address(12345)
    assert a1 == a2
    assert a1.startswith("0x") and len(a1) == 42


def test_compute_address_different_tg_id(monkeypatch):
    _enable_create2(monkeypatch)
    a1 = create2._compute_address(100)
    a2 = create2._compute_address(200)
    assert a1 != a2


def test_compute_address_different_factory(monkeypatch):
    _enable_create2(monkeypatch)
    a1 = create2._compute_address(999)
    monkeypatch.setattr(create2, "FACTORY_ADDRESS", "0x" + "ff" * 20)
    a2 = create2._compute_address(999)
    assert a1 != a2


def test_compute_address_different_bytecode(monkeypatch):
    _enable_create2(monkeypatch)
    a1 = create2._compute_address(999)
    other_bytecode = "0x" + "bb" * 45
    monkeypatch.setattr(create2, "MINIMAL_PROXY_BYTECODE", other_bytecode)
    a2 = create2._compute_address(999)
    assert a1 != a2


def test_compute_address_empty_when_no_factory(monkeypatch):
    monkeypatch.setattr(create2, "FACTORY_ADDRESS", None)
    assert create2._compute_address(123) == ""


# --------------------------------------------- test_get_deposit_address


def test_get_deposit_address_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(create2, "FACTORY_ADDRESS", None)
    assert create2.get_deposit_address(1) == ""


def test_get_deposit_address_returns_address_when_enabled(monkeypatch):
    _enable_create2(monkeypatch)
    addr = create2.get_deposit_address(42)
    assert addr.startswith("0x") and len(addr) == 42
    assert addr == create2._compute_address(42)


# ------------------------------------------ init code hash verification


def test_init_code_hash_matches_expected(monkeypatch):
    _enable_create2(monkeypatch)
    expected_hash = Web3.keccak(hexstr=COMPLETE_BYTECODE)
    salt = Web3.keccak(text=str(999))
    bytecode_hash = Web3.keccak(hexstr=COMPLETE_BYTECODE)
    manual_addr = Web3.keccak(
        b"\xff"
        + bytes.fromhex(FACTORY[2:])
        + salt
        + bytecode_hash
    )[12:]
    manual_checksum = Web3.to_checksum_address(manual_addr.hex())
    assert create2._compute_address(999) == manual_checksum
