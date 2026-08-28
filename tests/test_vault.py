"""TipBotVault on-chain treasury: proof of reserves, relayer daily cap, owner powers.

Runs against a real local EVM (EthereumTester + py-evm) with the real compiled
bytecode from contracts/. No mocks: every assert goes through the EVM.
"""
import ast
import os

import pytest
import solcx
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

CONTRACTS = os.path.join(os.path.dirname(__file__), "..", "contracts")
SOLC = "0.8.24"

USDC = 1_000_000


@pytest.fixture(scope="session")
def compiled():
    solcx.set_solc_version(SOLC)
    return solcx.compile_files(
        [
            os.path.join(CONTRACTS, "TipBotVault.sol"),
            os.path.join(CONTRACTS, "MiniUSDC.sol"),
        ],
        output_values=["abi", "bin"],
    )


def _artifact(compiled, name):
    for key, art in compiled.items():
        if key.endswith(":" + name):
            return art
    raise KeyError(name)


def _revert_data(msg):
    i = msg.find("b'")
    if i < 0:
        i = msg.find('b"')
    if i < 0:
        return b""
    try:
        return ast.literal_eval(msg[i:])
    except Exception:
        return b""


def expect_revert(call, sig):
    with pytest.raises(Exception) as ei:
        call()
    msg = str(ei.value)
    data = _revert_data(msg)
    assert data.startswith(Web3.keccak(text=sig)[:4]), f"{sig}: {msg}"


def mine(w3, tx):
    return w3.eth.wait_for_transaction_receipt(tx)


@pytest.fixture
def w3():
    return Web3(EthereumTesterProvider())


@pytest.fixture
def deploy(compiled, w3):
    def _deploy(daily_limit=500 * USDC):
        owner, relayer, alice, bob = w3.eth.accounts[:4]
        usdc_art = _artifact(compiled, "MiniUSDC")
        usdc = w3.eth.contract(abi=usdc_art["abi"], bytecode=usdc_art["bin"])
        usdc_addr = mine(w3, usdc.constructor().transact({"from": owner})).contractAddress
        usdc = w3.eth.contract(address=usdc_addr, abi=usdc_art["abi"])

        vault_art = _artifact(compiled, "TipBotVault")
        vault = w3.eth.contract(abi=vault_art["abi"], bytecode=vault_art["bin"])
        vault_addr = mine(
            w3, vault.constructor(usdc_addr, owner, relayer, daily_limit).transact({"from": owner})
        ).contractAddress
        vault = w3.eth.contract(address=vault_addr, abi=vault_art["abi"])
        return w3, usdc, vault, owner, relayer, alice, bob

    return _deploy


def mint(w3, usdc, to, amount):
    mine(w3, usdc.functions.mint(to, amount).transact({"from": w3.eth.accounts[0]}))


def deposit(w3, usdc, vault, who, amount):
    mine(w3, usdc.functions.transfer(vault.address, amount).transact({"from": who}))


# ---------- proof of reserves ----------


def test_deposit_is_reflected_in_total_reserves(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    mint(w3, usdc, alice, 1000 * USDC)
    assert vault.functions.totalReserves().call() == 0
    deposit(w3, usdc, vault, alice, 1000 * USDC)
    assert vault.functions.totalReserves().call() == 1000 * USDC
    # anyone can point a solver at the vault: the balance is public and readable
    assert usdc.functions.balanceOf(vault.address).call() == 1000 * USDC


def test_direct_transfer_counts_as_reserve(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    mint(w3, usdc, bob, 42 * USDC)
    # a user sends USDC straight to the vault address — still counted
    mine(w3, usdc.functions.transfer(vault.address, 42 * USDC).transact({"from": bob}))
    assert vault.functions.totalReserves().call() == 42 * USDC


# ---------- batch distribution ----------


def test_relayer_batches_payouts_within_limit(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    mint(w3, usdc, alice, 500 * USDC)
    deposit(w3, usdc, vault, alice, 500 * USDC)

    rec = mine(
        w3,
        vault.functions.batchDistribute([bob, alice], [100 * USDC, 250 * USDC]).transact(
            {"from": relayer}
        ),
    )

    assert usdc.functions.balanceOf(bob).call() == 100 * USDC
    assert usdc.functions.balanceOf(alice).call() == 250 * USDC
    assert vault.functions.totalReserves().call() == 150 * USDC
    assert vault.functions.spentTodayView().call() == 350 * USDC
    # Filter to the Distributed topic (and vault address): otherwise
    # eth-utils warns on every non-matching log (FakeUSDC Transfer etc.).
    topic = vault.events.Distributed.build_filter().event_topic
    vault_logs = {**rec, "logs": [lg for lg in rec["logs"]
                                  if str(lg["address"]).lower() == vault.address.lower()
                                  and lg["topics"] and lg["topics"][0] == topic]}
    events = vault.events.Distributed().process_receipt(vault_logs)
    assert {(e["args"]["recipient"], e["args"]["amount"]) for e in events} == {
        (bob, 100 * USDC),
        (alice, 250 * USDC),
    }


def test_relayer_rejected_over_daily_limit(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy(daily_limit=100 * USDC)
    mint(w3, usdc, alice, 500 * USDC)
    deposit(w3, usdc, vault, alice, 500 * USDC)

    mine(w3, vault.functions.batchDistribute([bob], [100 * USDC]).transact({"from": relayer}))
    # a single payout beyond the cap reverts
    expect_revert(lambda: vault.functions.batchDistribute([alice], [1 * USDC]).transact({"from": relayer}), "DailyLimitExceeded(uint256,uint256,uint256)")


def test_relayer_limit_resets_next_day(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy(daily_limit=100 * USDC)
    mint(w3, usdc, alice, 500 * USDC)
    deposit(w3, usdc, vault, alice, 500 * USDC)

    mine(w3, vault.functions.batchDistribute([bob], [100 * USDC]).transact({"from": relayer}))
    expect_revert(lambda: vault.functions.batchDistribute([bob], [1 * USDC]).transact({"from": relayer}), "DailyLimitExceeded(uint256,uint256,uint256)")

    now = w3.eth.get_block("latest")["timestamp"]
    w3.provider.ethereum_tester.time_travel(now + 86401)
    mine(w3, vault.functions.batchDistribute([bob], [50 * USDC]).transact({"from": relayer}))
    assert vault.functions.spentTodayView().call() == 50 * USDC


def test_owner_distributes_without_limit(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy(daily_limit=1 * USDC)
    mint(w3, usdc, alice, 500 * USDC)
    deposit(w3, usdc, vault, alice, 500 * USDC)
    mine(w3, vault.functions.batchDistribute([bob], [500 * USDC]).transact({"from": owner}))
    assert usdc.functions.balanceOf(bob).call() == 500 * USDC


def test_stranger_cannot_distribute(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    expect_revert(lambda: vault.functions.batchDistribute([bob], [1 * USDC]).transact({"from": alice}), "OnlyOwnerOrRelayer()")


def test_mismatched_arrays_rejected(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    expect_revert(lambda: vault.functions.batchDistribute([bob], [1 * USDC, 2 * USDC]).transact({"from": relayer}), "MismatchedArrays()")
    expect_revert(lambda: vault.functions.batchDistribute([], []).transact({"from": relayer}), "EmptyDistribution()")


def test_batch_reverts_when_reserves_insufficient(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    mint(w3, usdc, alice, 10 * USDC)
    deposit(w3, usdc, vault, alice, 10 * USDC)
    with pytest.raises(Exception, match="execution reverted"):
        vault.functions.batchDistribute([bob], [11 * USDC]).transact({"from": relayer})
    # nothing was spent, nothing was lost
    assert vault.functions.totalReserves().call() == 10 * USDC


# ---------- owner powers ----------


def test_only_owner_controls_roles_and_reserve(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    expect_revert(lambda: vault.functions.setDailyLimit(0).transact({"from": relayer}), "OnlyOwner()")
    expect_revert(lambda: vault.functions.setRelayer(bob).transact({"from": alice}), "OnlyOwner()")
    expect_revert(lambda: vault.functions.withdrawReserve(alice, 1).transact({"from": bob}), "OnlyOwner()")

    mine(w3, vault.functions.setDailyLimit(999 * USDC).transact({"from": owner}))
    mine(w3, vault.functions.setRelayer(bob).transact({"from": owner}))
    assert vault.functions.dailyLimit().call() == 999 * USDC
    assert vault.functions.relayer().call() == bob


def test_ownership_transfer(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    mine(w3, vault.functions.transferOwnership(bob).transact({"from": owner}))
    assert vault.functions.pendingOwner().call() == bob
    assert vault.functions.owner().call() == owner  # not transferred yet
    # non-pending-owner cannot accept
    expect_revert(lambda: vault.functions.acceptOwnership().transact({"from": alice}), "NotPendingOwner()")
    mine(w3, vault.functions.acceptOwnership().transact({"from": bob}))
    assert vault.functions.owner().call() == bob
    assert vault.functions.pendingOwner().call() == "0x" + "00" * 20
    expect_revert(lambda: vault.functions.setDailyLimit(1).transact({"from": owner}), "OnlyOwner()")
    mine(w3, vault.functions.setDailyLimit(1).transact({"from": bob}))
    # zero-address cannot be proposed
    expect_revert(lambda: vault.functions.transferOwnership("0x" + "00" * 20).transact({"from": bob}), "OnlyOwner()")


def test_reserve_withdrawal(deploy):
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    mint(w3, usdc, alice, 100 * USDC)
    deposit(w3, usdc, vault, alice, 100 * USDC)
    mine(w3, vault.functions.withdrawReserve(owner, 40 * USDC).transact({"from": owner}))
    assert vault.functions.totalReserves().call() == 60 * USDC
    assert usdc.functions.balanceOf(owner).call() == 40 * USDC


def test_batch_distribute_skips_blacklisted_recipient(deploy):
    """USDC-style blacklist reverts on transfer — the batch must SKIP that
    recipient, pay the rest, and count only successful payouts against the
    relayer window (pre-fix: one bad address reverted the whole batch and
    bricked the daily window)."""
    w3, usdc, vault, owner, relayer, alice, bob = deploy()
    mint(w3, usdc, alice, 500 * USDC)
    deposit(w3, usdc, vault, alice, 500 * USDC)

    bad = w3.eth.accounts[6]
    usdc.functions.setBlacklisted(bad).transact({"from": w3.eth.accounts[0]})

    rec = mine(
        w3,
        vault.functions.batchDistribute(
            [bad, bob, alice], [70 * USDC, 100 * USDC, 250 * USDC]
        ).transact({"from": relayer}),
    )

    assert usdc.functions.balanceOf(bob).call() == 100 * USDC
    assert usdc.functions.balanceOf(alice).call() == 250 * USDC
    assert usdc.functions.balanceOf(bad).call() == 0
    # Only successful payouts consume the relayer's daily window.
    assert vault.functions.spentTodayView().call() == 350 * USDC

    skipped = vault.events.DistributeSkipped().process_receipt(
        {**rec, "logs": [lg for lg in rec["logs"]
                         if str(lg["address"]).lower() == vault.address.lower()
                         and lg["topics"] and lg["topics"][0] == vault.events.DistributeSkipped.build_filter().event_topic]}
    )
    assert skipped[0]["args"]["recipient"] == bad
    assert skipped[0]["args"]["amount"] == 70 * USDC
