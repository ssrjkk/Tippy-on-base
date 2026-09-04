#!/usr/bin/env python
"""Live smoke test for the P2 Smart Wallet gasless approve+buy flow (Sepolia).

WALKS the whole P2 assortment:
   1. read-only: chain, EntryPoint, factory, paymaster, USDC, relayer + smart
      account state, paymaster deposit at the EntryPoint.
   2. optional deploy of OutcomeMarket (only if OUTCOME_MARKET_ADDRESS empty).
   3. optional create of a test on-chain market.
   4. optional fund of the test SmartAccount with USDC (from the hot wallet).
   5. the key step: gasless smart_buy -> handleOps with VerifyingPaymaster
      sponsorship -> receipt verification + post-trade balance/proof.

EVERY broadcast is gated behind an explicit CLI step flag. Nothing is sent
unless you ask for it (`--deploy-market`, `--create-market`, `--fund`,
`--buy`). Run without flags for the read-only summary.

Usage:
    python scripts/smoke_smart_buy.py                       # read-only
    python scripts/smoke_smart_buy.py --deploy-market       # 1 broadcast
    python scripts/smoke_smart_buy.py --create-market       # 1 broadcast
    python scripts/smoke_smart_buy.py --fund 5              # 1 broadcast (USDC)
    python scripts/smoke_smart_buy.py --buy <mid> <out> <shares> <max_usdc>

Environment: reads .env via bot.config (BASE_RPC_URL=sepolia.base.org,
HOT_WALLET_KEY, SMART_WALLET_*, OUTCOME_MARKET_ADDRESS).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Safer smoke: require explicit Sepolia unless overridden.
os.environ.setdefault("EXPECTED_CHAIN_ID", "84532")
os.environ.setdefault("BASE_RPC_URL", "https://sepolia.base.org")

from bot import base, config  # noqa: E402
from bot import smart_wallet as sw  # noqa: E402

MICRO = 10 ** config.USDC_DECIMALS
TEST_TG = 987654321123  # the account we already deployed in earlier testing


def _banner(title: str) -> None:
    print(f"\n=== {title} ===")


def read_only() -> None:
    cid = base.chain_id_sync()
    print(f"chain_id: {cid} (sepolia={cid == 84532}, mainnet={cid == 8453})")
    hot = base.hot_wallet()
    print(f"relayer (hot): {hot}")
    print(f"relayer ETH: {base.eth_balance_sync(hot):.6f}")
    print(f"relayer USDC: {base.token_balance_sync(hot) / MICRO:.2f}")
    print(f"EntryPoint:   {config.SMART_WALLET_ENTRYPOINT}")
    print(f"Factory:      {config.SMART_WALLET_FACTORY_ADDRESS}")
    print(f"Paymaster:    {config.SMART_WALLET_PAYMASTER_ADDRESS}")

    if not (config.SMART_WALLET_FACTORY_ADDRESS and config.SMART_WALLET_PAYMASTER_ADDRESS):
        print("!! Factory/Paymaster not configured — nothing to smoke.")
        return

    smart = sw.predict_address(TEST_TG)
    deployed = sw.is_deployed(TEST_TG)
    print(f"smart account ({TEST_TG}): {smart} deployed={deployed}")
    if deployed:
        print(f"  smart USDC: {sw.smart_balance(TEST_TG) / MICRO:.2f}")
        print(f"  nonce (EntryPoint): {sw.smart_nonce(TEST_TG)}")

    # Paymaster deposit at the EntryPoint (must be able to sponsor gas).
    ep = sw._entrypoint()
    pm = config.SMART_WALLET_PAYMASTER_ADDRESS
    try:
        dep = ep.functions.balanceOf(sw.Web3.to_checksum_address(pm)).call()
        print(f"paymaster deposit (EntryPoint): {dep / 1e18:.6f} ETH")
        if dep < 5_000_000_000_000_000:  # < 0.005 ETH
            print("  WARN: low paymaster deposit — re-fund before gasless buys.")
    except Exception as e:  # noqa: BLE001
        print(f"  (could not read paymaster deposit: {e})")

    if config.OUTCOME_MARKET_ADDRESS:
        from bot import onchain_market as om
        print(f"OutcomeMarket: {config.OUTCOME_MARKET_ADDRESS}")
        try:
            c = om._market_contract(om._w3())
            n = c.functions.nextMarketId().call() - 1
            print(f"  on-chain markets created: {n}")
        except Exception as e:  # noqa: BLE001
            print(f"  (market read failed: {e})")
    else:
        print("OutcomeMarket: not configured — need `--deploy-market` for a real buy")


def deploy_market() -> None:
    if config.OUTCOME_MARKET_ADDRESS:
        print(f"OutcomeMarket already set: {config.OUTCOME_MARKET_ADDRESS} (skipping --deploy-market)")
        return
    print("Deploying OutcomeMarket on this chain from HOT_WALLET_KEY (owner=relayer).")
    rpc = config.BASE_RPC_URL
    cmd = [sys.executable, str(ROOT / "scripts" / "deploy_outcome_market.py"),
           "--rpc", rpc, "--usdc", config.USDC_ADDRESS, "--wire-env"]
    # The deploy script guards chain with EXPECTED_CHAIN_ID in .env (84532).
    os.execv(sys.executable, cmd)


def create_market(n_outcomes: int, subsidy_usdc: float) -> None:
    import asyncio

    from bot import onchain_market as om

    if not config.OUTCOME_MARKET_ADDRESS:
        raise SystemExit("OUTCOME_MARKET_ADDRESS not set — deploy a market first (--deploy-market)")

    async def _run():
        return await om.create_market(
            n_outcomes,
            round(subsidy_usdc * MICRO),
            closes_at=int(__import__("time").time()) + 7 * 86400,
            private_key=config.HOT_WALLET_KEY,
        )
    tx = asyncio.run(_run())
    print(f"market created tx: {tx}")


def fund(amount_usdc: float) -> None:
    if not sw.is_deployed(TEST_TG):
        print(f"SmartAccount for {TEST_TG} not deployed — deploying via factory...")
        addr = sw.create_account(TEST_TG)
        print(f"  deployed {addr}")
    smart = sw.predict_address(TEST_TG)
    amt_micro = round(amount_usdc * MICRO)
    relayer_usdc = base.token_balance_sync(base.hot_wallet())
    if relayer_usdc < amt_micro:
        raise SystemExit(f"relayer has only {relayer_usdc / MICRO:.2f} USDC, need {amount_usdc}")
    tx = base._send_usdc_sync(smart, amt_micro)
    print(f"funded {amount_usdc} USDC -> {smart} tx: {tx}")
    print(f"new smart USDC: {sw.smart_balance(TEST_TG) / MICRO:.2f}")


def buy(market_id: int, outcome: int, shares: int, max_cost_usdc: float) -> None:
    if not config.OUTCOME_MARKET_ADDRESS:
        raise SystemExit("OUTCOME_MARKET_ADDRESS not set — deploy a market first")
    if not sw.is_deployed(TEST_TG):
        raise SystemExit("SmartAccount not deployed — run --fund first")
    max_cost_micro = round(max_cost_usdc * MICRO)
    bal = sw.smart_balance(TEST_TG)
    if bal < max_cost_micro:
        raise SystemExit(f"smart USDC {bal / MICRO:.2f} < max_cost {max_cost_usdc}")

    _banner("GASLESS SMART BUY (handleOps + VerifyingPaymaster)")
    print(f"market={market_id} outcome={outcome} shares={shares} max_cost={max_cost_usdc} USDC")
    print(f"smart account: {sw.predict_address(TEST_TG)}")
    import asyncio
    tx_hash = asyncio.run(sw.smart_buy(
        TEST_TG, market_id, outcome, shares, max_cost_micro
    ))
    print(f"handleOps tx: {tx_hash}")
    print(f"post-trade smart USDC: {sw.smart_balance(TEST_TG) / MICRO:.2f}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--deploy-market", action="store_true", help="deploy OutcomeMarket (1 tx)")
    p.add_argument("--create-market", nargs=2, type=float, metavar=("N_OUTCOMES", "SUBSIDY_USDC"),
                   help="create a test market (1 tx)")
    p.add_argument("--fund", type=float, metavar="USDC", help="fund test SmartAccount with USDC (1 tx)")
    p.add_argument("--buy", nargs=4, type=float, metavar=("MID", "OUTCOME", "SHARES", "MAX_USDC"),
                   help="gasless smart buy (1 handleOps tx)")
    args = p.parse_args()

    _banner("READ-ONLY STATE")
    read_only()

    if args.deploy_market:
        _banner("DEPLOY MARKET")
        deploy_market()
    if args.create_market:
        _banner("CREATE MARKET")
        create_market(int(args.create_market[0]), args.create_market[1])
    if args.fund is not None:
        _banner("FUND SMART ACCOUNT")
        fund(args.fund)
    if args.buy:
        _banner("SMART BUY")
        buy(int(args.buy[0]), int(args.buy[1]), int(args.buy[2]), args.buy[3])

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
