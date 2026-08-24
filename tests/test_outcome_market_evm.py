"""OutcomeMarket + LMSR on a real local EVM (EthereumTester/py-evm).

The 2026-08-24 security fixes (signed-cast wrap, cancelExpired refund theft)
verified end-to-end with real compiled bytecode вЂ” the same scenarios as
contracts/test/forge/SecurityFixes.t.sol, but executable in CI without foundry.
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

FAKE_USDC_SRC = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
contract FakeUSDC is IERC20 {
    mapping(address => uint256) public override balanceOf;
    mapping(address => mapping(address => uint256)) public override allowance;
    uint256 public override totalSupply;
    string public constant name = "Fake USDC";
    string public constant symbol = "USDC";
    uint8 public constant decimals = 6;
    function mint(address to, uint256 a) external { balanceOf[to] += a; totalSupply += a; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transfer(address t, uint256 a) external returns (bool) { _x(msg.sender, t, a); return true; }
    function transferFrom(address f, address t, uint256 a) external returns (bool) {
        uint256 al = allowance[f][msg.sender];
        if (al != type(uint256).max) { require(al >= a, "allowance"); allowance[f][msg.sender] = al - a; }
        _x(f, t, a); return true;
    }
    function _x(address f, address t, uint256 a) private { require(balanceOf[f] >= a, "bal"); balanceOf[f] -= a; balanceOf[t] += a; }
}
"""


@pytest.fixture(scope="session")
def compiled():
    solcx.set_solc_version(SOLC)

    def load(name):
        with open(os.path.join(CONTRACTS, name), encoding="utf-8") as f:
            return f.read()

    inp = {
        "language": "Solidity",
        "sources": {
            "OutcomeMarket.sol": {"content": load("OutcomeMarket.sol")},
            "LMSR.sol": {"content": load("LMSR.sol")},
            "FakeUSDC.sol": {"content": FAKE_USDC_SRC},
        },
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "remappings": [
                "@openzeppelin/contracts/=contracts/lib/openzeppelin-contracts/contracts/",
                "prb-math/=contracts/lib/prb-math/src/",
            ],
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    out = solcx.compile_standard(inp, allow_paths=os.path.abspath(CONTRACTS))
    return out["contracts"]


def _artifact(compiled, fname, cname):
    return compiled[fname][cname]


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
    sel = Web3.keccak(text=sig)[:4]
    with pytest.raises(Exception) as ei:
        call()
    data = _revert_data(str(ei.value))
    assert data.startswith(sel), f"expected {sig}, got: {str(ei.value)[:300]}"


@pytest.fixture
def w3():
    return Web3(EthereumTesterProvider())


@pytest.fixture
def env(compiled, w3):
    owner, trader1, trader2, nobody = w3.eth.accounts[:4]

    usdc_art = _artifact(compiled, "FakeUSDC.sol", "FakeUSDC")
    usdc_c = w3.eth.contract(abi=usdc_art["abi"], bytecode=usdc_art["evm"]["bytecode"]["object"])
    tx = usdc_c.constructor().transact({"from": owner})
    usdc = w3.eth.contract(
        address=w3.eth.wait_for_transaction_receipt(tx).contractAddress, abi=usdc_art["abi"]
    )

    mkt_art = _artifact(compiled, "OutcomeMarket.sol", "OutcomeMarket")
    mkt_c = w3.eth.contract(abi=mkt_art["abi"], bytecode=mkt_art["evm"]["bytecode"]["object"])
    tx = mkt_c.constructor(usdc.address, owner).transact({"from": owner})
    mkt = w3.eth.contract(
        address=w3.eth.wait_for_transaction_receipt(tx).contractAddress, abi=mkt_art["abi"]
    )

    for who in (owner, trader1, trader2):
        usdc.functions.mint(who, 1_000_000 * USDC).transact({"from": owner})

    class Env:
        pass

    e = Env()
    e.w3, e.usdc, e.mkt = w3, usdc, mkt
    e.owner, e.t1, e.t2, e.nobody = owner, trader1, trader2, nobody
    e.closes_at = int(w3.eth.get_block("latest").timestamp) + 3600

    def create(n_outcomes=2, subsidy=50 * USDC):
        usdc.functions.approve(mkt.address, subsidy).transact({"from": owner})
        tx = mkt.functions.createMarket(n_outcomes, subsidy, e.closes_at).transact({"from": owner})
        rcpt = w3.eth.wait_for_transaction_receipt(tx)
        return mkt.events.MarketCreated().process_receipt(rcpt)[0]["args"]["marketId"]

    def buy(who, mid, outcome, shares):
        usdc.functions.approve(mkt.address, 10_000_000 * USDC).transact({"from": who})
        cost = mkt.functions.quoteBuy(mid, outcome, shares).call({"from": who})
        mkt.functions.buy(mid, outcome, shares, cost).transact({"from": who})
        return cost

    e.create, e.buy = create, buy

    def warp(ts):
        w3.provider.ethereum_tester.time_travel(ts)
        w3.provider.ethereum_tester.mine_block()

    e.warp = warp
    e.token = lambda mid, idx: mid * 256 + idx
    return e


# ---------------------------------------------------------------------------
# Happy path: trade -> resolve -> redeem at $1 par
# ---------------------------------------------------------------------------

def test_full_market_lifecycle(env):
    e = env
    mid = e.create()
    assert e.usdc.functions.balanceOf(e.mkt.address).call() == 50 * USDC

    cost = e.buy(e.t1, mid, 0, 2_000_000)  # 2 shares of par
    assert cost > 0
    assert e.mkt.functions.balanceOf(e.t1, e.token(mid, 0)).call() == 2_000_000

    # price moved toward outcome 0 after buying it
    p0_before = e.mkt.functions.priceOf(mid, 0).call()
    assert p0_before > 500_000_000_000_000_000 // 10  # sanity: nonzero-ish

    e.warp(e.closes_at + 1)
    e.mkt.functions.oracleResolve(mid, 0).transact({"from": e.owner})

    bal_before = e.usdc.functions.balanceOf(e.t1).call()
    payout = e.mkt.functions.redeem(mid).transact({"from": e.t1})
    payout = e.mkt.events.Redeemed().process_receipt(
        e.w3.eth.wait_for_transaction_receipt(payout)
    )[0]["args"]["usdcMicro"]
    assert payout == 2_000_000  # 1 micro-share == 1 micro-USDC
    assert e.usdc.functions.balanceOf(e.t1).call() == bal_before + 2_000_000


# ---------------------------------------------------------------------------
# VULN-1 regression: signed-cast wrap -> free giant buys
# ---------------------------------------------------------------------------

def test_reject_huge_shares(env):
    e = env
    mid = e.create()
    huge = 2 ** 255 + 1

    # Layer 1: LMSR library itself refuses (reachable via quoteBuy).
    expect_revert(
        lambda: e.mkt.functions.quoteBuy(mid, 0, huge).call({"from": e.t1}),
        "SharesTooLarge()",
    )

    # Layer 2: market-level cap rejects before any math.
    e.usdc.functions.approve(e.mkt.address, 10_000_000 * USDC).transact({"from": e.t1})
    expect_revert(
        lambda: e.mkt.functions.buy(mid, 0, huge, 2 ** 256 - 1).transact({"from": e.t1}),
        "InvalidShares()",
    )


def test_reject_supply_cap(env):
    e = env
    mid = e.create()
    cap = e.mkt.functions.MAX_SUPPLY_PER_OUTCOME().call()
    expect_revert(lambda: e.buy(e.t1, mid, 0, cap + 1), "InvalidShares()")


def test_reject_zero_shares_buy(env):
    e = env
    mid = e.create()
    expect_revert(lambda: e.buy(e.t1, mid, 0, 0), "InvalidShares()")


def test_normal_sized_trades_still_work(env):
    e = env
    mid = e.create()
    cost = e.buy(e.t1, mid, 0, 1_000_000)
    assert cost > 0 and cost < 2 * USDC  # ~$1 for the first share at even odds


# ---------------------------------------------------------------------------
# VULN-2 regression: cancelExpired must refund HOLDERS, not the creator
# ---------------------------------------------------------------------------

def test_cancel_expired_pays_trader_not_creator(env):
    e = env
    mid = e.create()

    cost = e.buy(e.t1, mid, 0, 5_000_000)
    escrow = e.usdc.functions.balanceOf(e.mkt.address).call()
    assert escrow == 50 * USDC + cost

    creator_bal = e.usdc.functions.balanceOf(e.owner).call()
    t1_bal = e.usdc.functions.balanceOf(e.t1).call()

    e.warp(e.closes_at + int(e.mkt.functions.EXPIRY_WINDOW().call()) + 5)
    e.mkt.functions.cancelExpired(mid).transact({"from": e.nobody})

    # THE PoC: creator received nothing at cancel time.
    assert e.usdc.functions.balanceOf(e.owner).call() == creator_bal, "creator stole the refund"

    claimed = e.mkt.events.CancelClaimed().process_receipt(
        e.w3.eth.wait_for_transaction_receipt(
            e.mkt.functions.claimCancelled(mid).transact({"from": e.t1})
        )
    )[0]["args"]["usdcMicro"]
    assert claimed > 0
    assert claimed <= escrow
    assert e.usdc.functions.balanceOf(e.t1).call() == t1_bal + claimed
    assert e.mkt.functions.unclaimedEscrowMicro(mid).call() == escrow - claimed


def test_cancel_expired_no_holders_subsidy_to_creator(env):
    e = env
    mid = e.create()
    creator_bal = e.usdc.functions.balanceOf(e.owner).call()

    e.warp(e.closes_at + int(e.mkt.functions.EXPIRY_WINDOW().call()) + 5)
    e.mkt.functions.cancelExpired(mid).transact({"from": e.nobody})

    assert e.usdc.functions.balanceOf(e.owner).call() - creator_bal == 50 * USDC
    assert e.mkt.functions.unclaimedEscrowMicro(mid).call() == 0


def test_two_traders_conservation_dust_to_creator(env):
    e = env
    mid = e.create(n_outcomes=3)
    e.buy(e.t1, mid, 0, 4_000_000)
    e.buy(e.t2, mid, 1, 6_000_000)

    escrow = e.usdc.functions.balanceOf(e.mkt.address).call()
    assert escrow > 50 * USDC

    creator_bal = e.usdc.functions.balanceOf(e.owner).call()
    t1_bal, t2_bal = e.usdc.functions.balanceOf(e.t1).call(), e.usdc.functions.balanceOf(e.t2).call()

    e.warp(e.closes_at + int(e.mkt.functions.EXPIRY_WINDOW().call()) + 5)
    e.mkt.functions.cancelExpired(mid).transact({"from": e.nobody})
    assert e.usdc.functions.balanceOf(e.owner).call() == creator_bal

    r1 = e.mkt.functions.claimCancelled(mid).transact({"from": e.t1})
    c1 = e.mkt.events.CancelClaimed().process_receipt(
        e.w3.eth.wait_for_transaction_receipt(r1))[0]["args"]["usdcMicro"]
    r2 = e.mkt.functions.claimCancelled(mid).transact({"from": e.t2})
    c2 = e.mkt.events.CancelClaimed().process_receipt(
        e.w3.eth.wait_for_transaction_receipt(r2))[0]["args"]["usdcMicro"]

    assert c1 > 0 and c2 > 0
    # Conservation: payouts + dust swept to creator == whole escrow; drained.
    assert e.usdc.functions.balanceOf(e.mkt.address).call() == 0
    assert e.usdc.functions.balanceOf(e.t1).call() == t1_bal + c1
    assert e.usdc.functions.balanceOf(e.t2).call() == t2_bal + c2
    assert e.usdc.functions.balanceOf(e.owner).call() - creator_bal == escrow - c1 - c2
    assert e.mkt.functions.unclaimedEscrowMicro(mid).call() == 0


def test_claim_without_position_reverts(env):
    e = env
    mid = e.create()
    e.buy(e.t1, mid, 0, 1_000_000)

    e.warp(e.closes_at + int(e.mkt.functions.EXPIRY_WINDOW().call()) + 5)
    e.mkt.functions.cancelExpired(mid).transact({"from": e.nobody})

    expect_revert(lambda: e.mkt.functions.claimCancelled(mid).transact({"from": e.t2}),
                  "NothingToRedeem()")


def test_trading_blocked_after_cancel(env):
    e = env
    mid = e.create()
    e.warp(e.closes_at + int(e.mkt.functions.EXPIRY_WINDOW().call()) + 5)
    e.mkt.functions.cancelExpired(mid).transact({"from": e.nobody})
    expect_revert(lambda: e.buy(e.t1, mid, 0, 100), "AlreadyCancelled()")
