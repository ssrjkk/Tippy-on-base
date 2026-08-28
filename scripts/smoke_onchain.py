"""Live smoke test against Base mainnet (read-only, no signing, no funds moved)."""
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("BOT_TOKEN", "0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
os.environ.setdefault("HOT_WALLET_KEY", "0x" + "11" * 32)
os.environ.setdefault("WALLET_ENC_KEY", "a" * 32)

from bot import base, config  # noqa: E402

cid = base.chain_id_sync()
print(f"chain_id: {cid} (is Base mainnet: {cid == 8453})")
print(f"assert_base_chain: {base.assert_base_chain_sync()}")

bn = base.get_block_number_sync()
print(f"block_number: {bn}")

blk = base.get_block_sync()
bf = blk["base_fee_gwei"]
print(f"latest block: #{blk['number']}, ts={blk['timestamp']}, txs={blk['transactions']}, base_fee={bf:.3f} gwei")

fees = base.eip1559_fees_sync()
print(f"EIP-1559 fees: base={fees['base_fee_gwei']:.4f}, max={fees['max_fee_gwei']:.4f} gwei")

hot_usdc = base.token_balance_sync(base.hot_wallet())
print(f"hot wallet USDC balance: {hot_usdc / 1e6}")
hot_eth = base.eth_balance_sync(base.hot_wallet())
print(f"hot wallet ETH balance: {hot_eth:.8f}")
print(f"nonce (pending): {base.nonce_sync(base.hot_wallet())}")
print(f"is_contract(hot): {base.is_contract_sync(base.hot_wallet())}")
print(f"is_contract(USDC): {base.is_contract_sync(base.USDC)}")

ts = base.erc20_total_supply_sync()
print(f"USDC total supply: {ts / 1e6:,.0f} USDC")

meta = base.token_meta_sync(base.USDC)
print(f"USDC meta: {meta['symbol']} / {meta['decimals']} decimals ({meta['name']})")

eth_price = base.get_eth_price_usd_sync()
print(f"Chainlink ETH/USD: {eth_price}")
usdc_price = base.get_usdc_price_usd_sync()
print(f"Chainlink USDC/USD: {usdc_price}")

seq_ok = base.l2_sequencer_ok_sync()
print(f"L2 sequencer healthy: {seq_ok}")
if seq_ok is False:
    print("WARNING: sequencer outage reported — oracle reads may be stale")

# Basenames: resolve well-known registered names on Base.
for name in ("jesse.base.eth", "barmstrong.base.eth"):
    resolved = base.resolve_basename_sync(name)
    print(f"resolve_basename('{name}'): {resolved}")
    if resolved:
        rev = base.reverse_basename_sync(resolved)
        print(f"reverse_basename({resolved[:10]}...): {rev}")

status = base._tx_status_sync("0x" + "ab" * 32)
print(f"tx_status(random unknown hash): {status}")

print("\nALL LIVE READS OK")

# ---------------------------------------------------------------------------
# On-chain markets (OutcomeMarket) — read-only smoke of the Polymarket layer
# ---------------------------------------------------------------------------

if getattr(config, "OUTCOME_MARKET_ADDRESS", None):
    from bot import onchain_market as om  # noqa: E402

    print(f"outcome market: {config.OUTCOME_MARKET_ADDRESS}")
    c = om._market_contract(om._w3())
    created = c.functions.nextMarketId().call() - 1
    print(f"markets created on-chain: {created}")
    import asyncio

    for mid in range(1, min(created, 3) + 1):
        try:
            info = om.get_market_info(mid)
            n = info["num_outcomes"]
            prices = asyncio.run(om.market_prices(mid, n)) if n else []
            pcts = "/".join(f"{float(p) * 100:.1f}%" for p in prices) if prices else "-"
            state = ("resolved" if info["resolved"] else
                     "cancelled" if info["cancelled"] else
                     "disputed" if info["disputed"] else "open")
            print(f"  market #{mid}: b={info['b']}, escrow={info['escrow_micro'] / 1e6:.2f} USDC, "
                  f"state={state}, closes={info['closes_at']}, prices={pcts}")
        except Exception as e:
            print(f"  market #{mid}: read failed: {e}")
else:
    print("outcome market: not configured (OUTCOME_MARKET_ADDRESS empty) — Polymarket layer idle")
