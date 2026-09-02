#!/usr/bin/env python
"""Full-cycle smoke test for OutcomeMarket.

Deploys TestUSDC + OutcomeMarket, runs createMarket -> buy -> ownerResolve -> redeem.
Uses minimal ABIs to avoid web3.py decoding issues with full compiled ABIs.

Usage:
    python scripts/smoke_full_cycle.py --key <private_key_hex>
    SMOKE_KEY=<hex> python scripts/smoke_full_cycle.py

Environment:
    SMOKE_KEY  - deployer private key (hex, no 0x prefix)
    SMOKE_RPC  - RPC endpoint (default: https://sepolia.base.org)
    SMOKE_CHAIN - chain id (default: 84532)
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*MismatchedABI.*")

import solcx
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from web3 import Web3

ROOT = Path(__file__).resolve().parent.parent
solcx.set_solc_version("0.8.24")

# Minimal ABIs - only functions we actually call
ERC20_ABI = json.loads("""[
    {"stateMutability":"nonpayable","type":"function","name":"mint","inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[]},
    {"stateMutability":"view","type":"function","name":"balanceOf","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"stateMutability":"view","type":"function","name":"totalSupply","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
    {"stateMutability":"view","type":"function","name":"decimals","inputs":[],"outputs":[{"name":"","type":"uint8"}]},
    {"stateMutability":"nonpayable","type":"function","name":"approve","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
    {"stateMutability":"nonpayable","type":"function","name":"transfer","inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
    {"stateMutability":"view","type":"function","name":"allowance","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"anonymous":false,"inputs":[{"indexed":true,"name":"from","type":"address"},{"indexed":true,"name":"to","type":"address"},{"indexed":false,"name":"value","type":"uint256"}],"name":"Transfer","type":"event"}
]""")

MKT_ABI = json.loads("""[
    {"stateMutability":"view","type":"function","name":"owner","inputs":[],"outputs":[{"name":"","type":"address"}]},
    {"stateMutability":"view","type":"function","name":"usdc","inputs":[],"outputs":[{"name":"","type":"address"}]},
    {"stateMutability":"view","type":"function","name":"MIN_SUBSIDY_MICRO","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
    {"stateMutability":"view","type":"function","name":"MAX_OUTCOMES","inputs":[],"outputs":[{"name":"","type":"uint8"}]},
    {"stateMutability":"view","type":"function","name":"markets","inputs":[{"name":"","type":"uint256"}],"outputs":[{"name":"numOutcomes","type":"uint8"},{"name":"resolved","type":"bool"},{"name":"winningOutcome","type":"uint8"},{"name":"closesAt","type":"uint64"},{"name":"b","type":"int256"},{"name":"creator","type":"address"},{"name":"escrowMicro","type":"uint256"},{"name":"resolvedAt","type":"uint64"},{"name":"disputed","type":"bool"},{"name":"cancelled","type":"bool"}]},
    {"stateMutability":"nonpayable","type":"function","name":"createMarket","inputs":[{"name":"numOutcomes","type":"uint8"},{"name":"subsidyMicro","type":"uint256"},{"name":"closesAt","type":"uint64"}],"outputs":[{"name":"marketId","type":"uint256"}]},
    {"stateMutability":"nonpayable","type":"function","name":"buy","inputs":[{"name":"marketId","type":"uint256"},{"name":"outcomeIdx","type":"uint8"},{"name":"shares","type":"uint256"},{"name":"maxCostMicro","type":"uint256"}],"outputs":[{"name":"costMicro","type":"uint256"}]},
    {"stateMutability":"nonpayable","type":"function","name":"ownerResolve","inputs":[{"name":"marketId","type":"uint256"},{"name":"winningOutcome","type":"uint8"}],"outputs":[]},
    {"stateMutability":"nonpayable","type":"function","name":"redeem","inputs":[{"name":"marketId","type":"uint256"}],"outputs":[{"name":"payoutMicro","type":"uint256"}]},
    {"stateMutability":"view","type":"function","name":"quoteBuy","inputs":[{"name":"marketId","type":"uint256"},{"name":"outcomeIdx","type":"uint8"},{"name":"shares","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
    {"stateMutability":"view","type":"function","name":"priceOf","inputs":[{"name":"marketId","type":"uint256"},{"name":"outcomeIdx","type":"uint8"}],"outputs":[{"name":"","type":"uint256"}]},
    {"stateMutability":"view","type":"function","name":"balanceOf","inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
    {"stateMutability":"view","type":"function","name":"totalSupply","inputs":[{"name":"id","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
    {"anonymous":false,"inputs":[{"indexed":true,"name":"marketId","type":"uint256"},{"indexed":true,"name":"creator","type":"address"},{"indexed":false,"name":"numOutcomes","type":"uint8"},{"indexed":false,"name":"subsidyMicro","type":"uint256"},{"indexed":false,"name":"b","type":"int256"},{"indexed":false,"name":"closesAt","type":"uint64"}],"name":"MarketCreated","type":"event"},
    {"anonymous":false,"inputs":[{"indexed":true,"name":"marketId","type":"uint256"},{"indexed":true,"name":"trader","type":"address"},{"indexed":false,"name":"outcomeIdx","type":"uint8"},{"indexed":false,"name":"isBuy","type":"bool"},{"indexed":false,"name":"shares","type":"uint256"},{"indexed":false,"name":"usdcMicro","type":"uint256"}],"name":"Traded","type":"event"},
    {"anonymous":false,"inputs":[{"indexed":true,"name":"marketId","type":"uint256"},{"indexed":false,"name":"winningOutcome","type":"uint8"}],"name":"OwnerResolved","type":"event"},
    {"anonymous":false,"inputs":[{"indexed":true,"name":"marketId","type":"uint256"},{"indexed":true,"name":"holder","type":"address"},{"indexed":false,"name":"shares","type":"uint256"},{"indexed":false,"name":"usdcMicro","type":"uint256"}],"name":"Redeemed","type":"event"}
]""")


MKT_FIELDS = ("numOutcomes","resolved","winningOutcome","closesAt","b","creator","escrowMicro","resolvedAt","disputed","cancelled")

def market_dict(raw):
    return dict(zip(MKT_FIELDS, raw))


def say(tag, msg):
    print(f"\n=== [{tag}] {msg} ===", flush=True)

def ok(cond, msg):
    print(f"    [{'OK' if cond else 'FAIL'}] {msg}", flush=True)
    if not cond:
        raise SystemExit(f"INVARIANT FAILED: {msg}")


def build_and_send(w3, acct, fn, retries=3):
    for attempt in range(retries):
        try:
            return _build_and_send_inner(w3, acct, fn)
        except Exception as e:
            if attempt < retries - 1:
                print(f"    [RETRY] {type(e).__name__}, attempt {attempt+2}/{retries}...", flush=True)
                time.sleep(3)
            else:
                raise


def _build_and_send_inner(w3, acct, fn):
    gas_est = fn.estimate_gas({"from": acct.address})
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    priority = max(w3.eth.gas_price - base_fee, 10**6)
    max_fee = base_fee * 2 + priority
    nonce = w3.eth.get_transaction_count(acct.address)
    tx = fn.build_transaction({
        "chainId": w3.eth.chain_id, "nonce": nonce, "gas": int(gas_est * 1.5),
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority,
    })
    signed = acct.sign_transaction(tx)
    th = w3.eth.send_raw_transaction(signed.raw_transaction)
    rc = w3.eth.wait_for_transaction_receipt(th, timeout=300, poll_latency=1)
    if rc.status != 1:
        raise SystemExit(f"TX REVERTED: {th.hex()}")
    print(f"      tx {th.hex()[:20]}\u2026 gas={rc.gasUsed} block={rc.blockNumber}")
    return rc


def build_and_send_raw(w3, acct, tx_dict):
    signed = acct.sign_transaction(tx_dict)
    th = w3.eth.send_raw_transaction(signed.raw_transaction)
    rc = w3.eth.wait_for_transaction_receipt(th, timeout=300, poll_latency=1)
    if rc.status != 1:
        raise SystemExit(f"TX REVERTED: {th.hex()}")
    print(f"      tx {th.hex()[:20]}\u2026 gas={rc.gasUsed} block={rc.blockNumber}")
    return rc


def deploy(w3, acct, abi, bytecode, *ctor_args):
    c = w3.eth.contract(abi=abi, bytecode=bytecode)
    rc = build_and_send(w3, acct, c.constructor(*ctor_args))
    for _ in range(20):
        try:
            code = w3.eth.get_code(rc.contractAddress)
            if code not in (b"", b"\x00"):
                return rc.contractAddress
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit(f"No code at {rc.contractAddress}")


def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Full-cycle smoke test for OutcomeMarket")
    p.add_argument("--key", help="Deployer private key (hex)")
    p.add_argument("--rpc", default=os.getenv("SMOKE_RPC", "https://sepolia.base.org"))
    p.add_argument("--chain", type=int, default=int(os.getenv("SMOKE_CHAIN", "84532")))
    p.add_argument("--market-close-secs", type=int, default=90, help="Seconds until market closes")
    return p.parse_args()


def main():
    args = _parse_args()
    key = args.key or os.getenv("SMOKE_KEY")
    if not key:
        print("ERROR: provide --key or set SMOKE_KEY env var", file=sys.stderr)
        sys.exit(1)
    key = key.strip().replace("0x", "")
    rpc = args.rpc
    chain_id = args.chain

    retry = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504, 429])
    adapter = HTTPAdapter(max_retries=retry)
    session = __import__("requests").Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    w3 = Web3(Web3.HTTPProvider(rpc, session=session, request_kwargs={"timeout": 30}))
    assert w3.is_connected(), "RPC unreachable"
    assert w3.eth.chain_id == chain_id
    acct = w3.eth.account.from_key(key)
    deployer = Web3.to_checksum_address(acct.address)
    bal = w3.eth.get_balance(deployer)
    print(f"deployer={deployer} balance={bal/1e18:.4f} ETH nonce={w3.eth.get_transaction_count(deployer)}")
    if bal < 0.005e18:
        raise SystemExit("insufficient balance for gas")

    report = {"network": f"chain_{chain_id}", "deployer": deployer}

    # ================= 1. Deploy TestUSDC =================
    say("1", "deploying TestUSDC")
    usdc_src = (ROOT / "scripts" / "TestUSDC.sol").read_text(encoding="utf-8")
    usdc_art = solcx.compile_standard(
        {"language": "Solidity", "sources": {"TestUSDC.sol": {"content": usdc_src}},
         "settings": {"optimizer": {"enabled": True, "runs": 200},
                      "remappings": ["@openzeppelin/contracts/=contracts/lib/openzeppelin-contracts/contracts/"],
                      "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}}}},
        allow_paths=str(ROOT / "contracts")
    )["contracts"]["TestUSDC.sol"]["TestUSDC"]
    usdc_addr = deploy(w3, acct, usdc_art["abi"], usdc_art["evm"]["bytecode"]["object"], 6)
    usdc = w3.eth.contract(address=usdc_addr, abi=ERC20_ABI)
    ok(usdc.functions.decimals().call() == 6, "decimals()==6")
    report["testusdc"] = usdc_addr

    # ================= 2. Deploy OutcomeMarket =================
    say("2", "deploying OutcomeMarket")
    cd = ROOT / "contracts"
    def load(n): return (cd / n).read_text(encoding="utf-8")
    mkt_art = solcx.compile_standard(
        {"language": "Solidity",
         "sources": {"OutcomeMarket.sol": {"content": load("OutcomeMarket.sol")},
                     "LMSR.sol": {"content": load("LMSR.sol")}},
         "settings": {"optimizer": {"enabled": True, "runs": 200},
                      "remappings": ["@openzeppelin/contracts/=contracts/lib/openzeppelin-contracts/contracts/",
                                     "prb-math/=contracts/lib/prb-math/src/"],
                      "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}}}},
        allow_paths=str(ROOT / "contracts")
    )["contracts"]["OutcomeMarket.sol"]["OutcomeMarket"]
    mkt_addr = deploy(w3, acct, mkt_art["abi"], mkt_art["evm"]["bytecode"]["object"],
                       usdc_addr, deployer)
    mkt = w3.eth.contract(address=mkt_addr, abi=MKT_ABI)
    ok(mkt.functions.owner().call() == deployer, "owner()==deployer")
    ok(mkt.functions.usdc().call().lower() == usdc_addr.lower(), "usdc()==TestUSDC")
    ok(mkt.functions.MIN_SUBSIDY_MICRO().call() == 10_000_000, "MIN_SUBSIDY==$10")
    ok(mkt.functions.MAX_OUTCOMES().call() == 8, "MAX_OUTCOMES==8")
    report["outcome_market"] = mkt_addr

    # ================= 3. Fund =================
    say("3", "fund trader + deployer via mint + transfer")
    trader = w3.eth.account.create()
    trader_addr = Web3.to_checksum_address(trader.address)
    FUND = 100_000_000
    build_and_send(w3, acct, usdc.functions.mint(deployer, FUND * 2))
    for _attempt in range(10):
        abi_bal = usdc.functions.balanceOf(deployer).call()
        if abi_bal == FUND * 2:
            break
        time.sleep(2)
    ok(abi_bal == FUND * 2, f"deployer has 2*FUND (got {abi_bal})")
    build_and_send(w3, acct, usdc.functions.transfer(trader_addr, FUND))
    for _attempt in range(10):
        tb = usdc.functions.balanceOf(trader_addr).call()
        db = usdc.functions.balanceOf(deployer).call()
        if tb == FUND and db == FUND:
            break
        time.sleep(2)
    ok(tb == FUND, "trader funded")
    ok(db == FUND, "deployer has FUND")
    report["trader"] = trader_addr

    say("3b", "fund trader with gas ETH")
    gas_fund_wei = int(0.01e18)
    build_and_send_raw(w3, acct, {
        "to": trader_addr, "value": gas_fund_wei,
        "chainId": chain_id, "nonce": w3.eth.get_transaction_count(deployer),
        "gas": 21000, "maxFeePerGas": w3.eth.gas_price,
        "maxPriorityFeePerGas": w3.eth.gas_price,
    })
    for _attempt in range(10):
        if w3.eth.get_balance(trader_addr) >= gas_fund_wei:
            break
        time.sleep(2)
    ok(w3.eth.get_balance(trader_addr) >= gas_fund_wei, "trader has gas ETH")

    # ================= 4. createMarket =================
    say("4", f"createMarket(2, subsidy=$10, closesAt=now+{args.market_close_secs}s)")
    closes_at = int(time.time()) + args.market_close_secs
    build_and_send(w3, acct, usdc.functions.approve(mkt_addr, 10**15))
    for _attempt in range(10):
        al = usdc.functions.allowance(deployer, mkt_addr).call()
        if al >= 10_000_000:
            break
        time.sleep(2)
    rc = build_and_send(w3, acct, mkt.functions.createMarket(2, 10_000_000, closes_at))
    time.sleep(2)
    evts = mkt.events.MarketCreated().process_receipt(rc)
    if not evts:
        raise SystemExit("MarketCreated event not found in receipt")
    ev = evts[0]["args"]
    market_id, b_evt = ev["marketId"], ev["b"]
    say("4b", f"invariants (id={market_id})")
    mm = market_dict(mkt.functions.markets(market_id).call())
    ok(mm["numOutcomes"] == 2, "numOutcomes==2")
    ok(not mm["resolved"], "resolved==false")
    ok(mm["closesAt"] == closes_at, "closesAt correct")
    ok(mm["creator"].lower() == deployer.lower(), "creator==deployer")
    ok(mm["escrowMicro"] == 10_000_000, f"escrow==subsidy ({mm['escrowMicro']})")
    ok(b_evt == mm["b"] and mm["b"] != 0, f"b=={mm['b']}")
    report["market_id"] = market_id
    report["b"] = mm["b"]

    # ================= 5. buy =================
    say("5", "trader buys 500_000 shares of outcome 1")
    SHARES = 500_000
    quote = mkt.functions.quoteBuy(market_id, 1, SHARES).call()
    print(f"      quote={quote} uUSDC")
    ok(0 < quote < SHARES, "0 < quote < par")
    tb = usdc.functions.balanceOf(trader_addr).call()
    smkt = usdc.functions.balanceOf(mkt_addr).call()
    build_and_send(w3, trader, usdc.functions.approve(mkt_addr, 10**15))
    for _attempt in range(10):
        tal = usdc.functions.allowance(trader_addr, mkt_addr).call()
        if tal >= 10_000_000:
            break
        time.sleep(2)
    cost_rc = build_and_send(w3, trader, mkt.functions.buy(market_id, 1, SHARES, 10**15))
    cost = mkt.events.Traded().process_receipt(cost_rc)[0]["args"]["usdcMicro"]
    ok(cost == quote, f"cost==quote ({cost})")
    time.sleep(1)
    say("5b", "invariants after buy")
    mm = market_dict(mkt.functions.markets(market_id).call())
    ok(mm["escrowMicro"] == 10_000_000 + cost, "escrow==subsidy+cost")
    ok(mkt.functions.balanceOf(trader_addr, market_id * 256 + 1).call() == SHARES, "holds SHARES outcome1")
    ok(mkt.functions.balanceOf(trader_addr, market_id * 256 + 0).call() == 0, "holds 0 outcome0")
    ok(usdc.functions.balanceOf(mkt_addr).call() == smkt + cost, "market usdc +cost")
    ok(usdc.functions.balanceOf(trader_addr).call() == tb - cost, "trader usdc -cost")
    p0, p1 = mkt.functions.priceOf(market_id, 0).call(), mkt.functions.priceOf(market_id, 1).call()
    ok(p1 > p0, f"price1>price0 ({p0}<{p1})")
    report["shares"] = SHARES
    report["buy_cost"] = cost

    # ================= 6. wait + resolve =================
    wait_secs = max(0, closes_at - int(time.time()))
    say("6", f"waiting {wait_secs}s for market close...")
    while int(time.time()) < closes_at:
        time.sleep(3)
    say("6b", "ownerResolve(winner=1)")
    build_and_send(w3, acct, mkt.functions.ownerResolve(market_id, 1))
    for _attempt in range(10):
        mm = market_dict(mkt.functions.markets(market_id).call())
        if mm["resolved"]:
            break
        time.sleep(3)
    ok(mm["resolved"], "resolved==true")
    ok(mm["winningOutcome"] == 1, "winner==1")
    ok(mm["resolvedAt"] > 0, "resolvedAt>0")

    # ================= 7. redeem =================
    say("7", "trader redeems winning shares")
    trac = build_and_send(w3, trader, mkt.functions.redeem(market_id))
    time.sleep(2)
    payout = mkt.events.Redeemed().process_receipt(trac)[0]["args"]["usdcMicro"]
    ok(payout == SHARES, f"payout==shares ({payout}=={SHARES})")
    for _attempt in range(10):
        burned = mkt.functions.balanceOf(trader_addr, market_id * 256 + 1).call()
        if burned == 0:
            break
        time.sleep(2)
    ok(burned == 0, f"winning tokens burned (still {burned})")
    mm = market_dict(mkt.functions.markets(market_id).call())
    ok(mm["escrowMicro"] == 10_000_000 + cost - SHARES, "escrow reduced by payout")

    # ================= 8. profit =================
    say("8", "profit check")
    ok(payout > cost, f"profit: payout={payout} > cost={cost} (profit={payout-cost})")

    report["payout"] = payout
    report["profit"] = payout - cost
    report["ok"] = True

    result_path = ROOT / "smoke_result.json"
    print("\n\n===== SMOKE FULL-CYCLE RESULT =====")
    print(json.dumps(report, indent=2))
    result_path.write_text(json.dumps(report, indent=2))
    print(f"\nDONE - full cycle green, all invariants hold. Result: {result_path}")


if __name__ == "__main__":
    main()
