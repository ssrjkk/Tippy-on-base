#!/usr/bin/env python
"""Deploy OutcomeMarket.sol to Base mainnet.

Reads credentials from .env (HOT_WALLET_KEY) or CLI overrides; never logs
the private key. Compiles with the exact same solcx/optimizer settings as
tests/test_outcome_market_evm.py, so the deployed runtime bytecode matches
the code that passed the executable EVM regression suite byte-for-byte.

Usage:
    python scripts/deploy_outcome_market.py --dry-run     # rehearse, no broadcast
    python scripts/deploy_outcome_market.py               # deploy + verify on-chain
    python scripts/deploy_outcome_market.py --wire-env    # ...and write .env

Exit codes: 0 ok, 1 failure, 2 insufficient gas balance.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # native USDC on Base


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV_FILE = load_env(ROOT / ".env")


def cfg(key: str, flag_val: str | None) -> str:
    if flag_val:
        return flag_val
    val = os.environ.get(key) or ENV_FILE.get(key)
    if not val:
        raise SystemExit(f"missing {key} (set in .env or pass --{key.lower().replace('_', '-')})")
    return val


def compile_market() -> dict:
    import solcx

    solcx.set_solc_version("0.8.24")
    contracts_dir = ROOT / "contracts"

    def load(name: str) -> str:
        return (contracts_dir / name).read_text(encoding="utf-8")

    inp = {
        "language": "Solidity",
        "sources": {
            "OutcomeMarket.sol": {"content": load("OutcomeMarket.sol")},
            "LMSR.sol": {"content": load("LMSR.sol")},
        },
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "remappings": [
                "@openzeppelin/contracts/=contracts/lib/openzeppelin-contracts/contracts/",
                "prb-math/=contracts/lib/prb-math/src/",
            ],
            "outputSelection": {
                "*": {"*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"]}
            },
        },
    }
    out = solcx.compile_standard(inp, allow_paths=str(contracts_dir))
    art = out["contracts"]["OutcomeMarket.sol"]["OutcomeMarket"]
    return {"abi": art["abi"], "bin": art["evm"]["bytecode"]["object"],
            "runtime": art["evm"]["deployedBytecode"]["object"]}


def wire_env(address: str) -> None:
    env_path = ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out, replaced = [], False
    for ln in lines:
        if ln.startswith("OUTCOME_MARKET_ADDRESS="):
            out.append(f"OUTCOME_MARKET_ADDRESS={address}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"OUTCOME_MARKET_ADDRESS={address}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[wire] OUTCOME_MARKET_ADDRESS written to {env_path}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rpc", default=None)
    p.add_argument("--owner", default=None, help="initial owner (default: deployer)")
    p.add_argument("--usdc", default=USDC_BASE)
    p.add_argument("--dry-run", action="store_true", help="build+sign only, do NOT broadcast")
    p.add_argument("--wire-env", action="store_true")
    p.add_argument("--out", default=None, help="write JSON result here")
    args = p.parse_args()

    from web3 import Web3

    rpc = args.rpc or ENV_FILE.get("BASE_RPC_URL") or "https://mainnet.base.org"
    key = cfg("HOT_WALLET_KEY", os.environ.get("DEPLOYER_KEY"))
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print("ERROR: RPC unreachable:", rpc)
        return 1
    chain_id = w3.eth.chain_id
    acct = w3.eth.account.from_key(key)
    owner = Web3.to_checksum_address(args.owner) if args.owner else acct.address
    usdc = Web3.to_checksum_address(args.usdc)

    art = compile_market()
    contract = w3.eth.contract(abi=art["abi"], bytecode=art["bin"])
    ctor = contract.constructor(usdc, owner)
    gas_est = ctor.estimate_gas({"from": acct.address})
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    priority = max(w3.eth.gas_price - base_fee, 10 ** 6)
    max_fee = base_fee * 2 + priority
    nonce = w3.eth.get_transaction_count(acct.address)
    tx = ctor.build_transaction({
        "chainId": chain_id,
        "nonce": nonce,
        "gas": int(gas_est * 1.2),
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = "0x" + signed.raw_transaction.hex()
    est_cost_eth = tx["gas"] * max_fee / 1e18
    bal = w3.eth.get_balance(acct.address)
    need_wei = tx["gas"] * max_fee

    print(f"chain={chain_id} deployer={acct.address} owner={owner} usdc={usdc}")
    print(f"gas_estimate={gas_est:,} (+20% headroom -> {tx['gas']:,})")
    print(f"max_fee={max_fee/1e9:.4f} gwei  estimated_cost={est_cost_eth:.8f} ETH")
    print(f"balance={bal/1e18:.8f} ETH")

    if bal < need_wei:
        print(f"INSUFFICIENT GAS: need ~{need_wei/1e18:.8f} ETH on {acct.address}")
        return 2

    if args.dry_run:
        print(f"DRY-RUN OK — would send tx {tx_hash[:20]}… (not broadcast)")
        return 0

    sent = w3.eth.send_raw_transaction(signed.raw_transaction)
    print("broadcast:", sent.hex(), "- waiting for receipt...")
    receipt = w3.eth.wait_for_transaction_receipt(sent, timeout=300, poll_latency=2)
    if receipt.status != 1:
        print("ERROR: deployment reverted, tx:", sent.hex())
        return 1
    address = receipt.contractAddress
    onchain_runtime = w3.eth.get_code(address).hex()[2:]
    if onchain_runtime != art["runtime"].lstrip("0x").lower():
        print("ERROR: deployed runtime bytecode MISMATCH vs local compile!")
        return 1

    mkt = w3.eth.contract(address=address, abi=art["abi"])
    checks = {
        "owner()": mkt.functions.owner().call() == owner,
        "usdc()": mkt.functions.usdc().call() == usdc,
        "MAX_SUPPLY_PER_OUTCOME==1e15": mkt.functions.MAX_SUPPLY_PER_OUTCOME().call() == 10 ** 15,
        "EXPIRY_WINDOW>0": mkt.functions.EXPIRY_WINDOW().call() > 0,
        "bytecode match": True,
    }
    for k, ok in checks.items():
        print(f"  check {k}: {'OK' if ok else 'FAIL'}")

    result = {
        "network": f"chain_{chain_id}",
        "address": address,
        "tx_hash": sent.hex(),
        "block": receipt.blockNumber,
        "gas_used": receipt.gasUsed,
        "cost_eth": receipt.gasUsed * receipt.effectiveGasPrice / 1e18,
        "owner": owner,
        "usdc": usdc,
        "verified": all(checks.values()),
    }
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.wire_env:
        wire_env(address)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
