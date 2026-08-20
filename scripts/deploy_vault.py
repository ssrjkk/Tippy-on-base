"""Deploy TipBotVault to Base and wire it into the bot.

Usage (Base mainnet):
    python scripts/deploy_vault.py

Requirements: OWNER key in .env (or --owner-key), the bot's HOT_WALLET_KEY
becomes the relayer, VAULT_ADDRESS is appended to .env automatically.

Steps:
    1. compile contracts/TipBotVault.sol
    2. deploy with owner = OWNER_ADDRESS, relayer = bot hot wallet
    3. setDailyLimit(VAULT_RELAYER_DAILY_USDC)
    4. append VAULT_ADDRESS=<vault> to .env (idempotent)
    5. print the vault address + next manual steps
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load .env BEFORE importing bot.config (config reads env vars at import time
# and load_dotenv() without an explicit path looks only in the CWD).
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import solcx  # noqa: E402
from web3 import Web3  # noqa: E402

from bot import base, config  # noqa: E402

SOLC = "0.8.24"
VAULT_ABI = None  # filled by _compile


def _fee_params(w3: Web3) -> dict:
    """EIP-1559 fees for Base (same strategy as bot/base.py): 2x latest base
    fee + 0.01 gwei priority tip — without these, web3 falls back to legacy
    gasPrice pricing."""
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    tip = w3.to_wei(0.01, "gwei")
    return {"maxPriorityFeePerGas": tip, "maxFeePerGas": base_fee * 2 + tip}


def _compile():
    global VAULT_ABI
    solcx.set_solc_version(SOLC)
    out = solcx.compile_files(
        [str(ROOT / "contracts" / "TipBotVault.sol")], output_values=["abi", "bin"]
    )
    art = next(v for k, v in out.items() if k.endswith(":TipBotVault"))
    VAULT_ABI = art["abi"]
    return art


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-key", help="owner private key (default: OWNER_KEY env)")
    parser.add_argument(
        "--daily-usdc", type=float, default=float(os.environ.get("VAULT_RELAYER_DAILY_USDC", "5000")),
        help="relayer daily distribution cap in USDC",
    )
    parser.add_argument("--rpc", default=config.BASE_RPC_URL)
    args = parser.parse_args()

    owner_key = args.owner_key or os.environ.get("OWNER_KEY")
    if not owner_key:
        print("OWNER_KEY is required (owner = vault multisig/safe address key)")
        return 1
    if not re_fullmatch_64(owner_key):
        print("OWNER_KEY must be 0x + 64 hex chars")
        return 1

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        print(f"cannot reach RPC: {args.rpc}")
        return 1

    art = _compile()
    owner_acct = w3.eth.account.from_key(owner_key)
    owner = owner_acct.address
    relayer = base.hot_wallet()
    usdc_addr = Web3.to_checksum_address(config.USDC_ADDRESS)

    print(f"owner   : {owner}")
    print(f"relayer : {relayer} (bot hot wallet)")
    print(f"usdc    : {usdc_addr}")
    print(f"daily   : {args.daily_usdc} USDC")
    print("deploying...")
    nonce = w3.eth.get_transaction_count(owner, "pending")
    daily_micro = int(args.daily_usdc * 10**config.USDC_DECIMALS)
    contract = w3.eth.contract(abi=VAULT_ABI, bytecode=art["bin"])
    tx = contract.constructor(usdc_addr, owner, relayer, daily_micro).build_transaction(
        {"from": owner, "nonce": nonce, "chainId": w3.eth.chain_id, **_fee_params(w3)}
    )
    signed = owner_acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print("waiting for receipt:", w3.to_hex(tx_hash))
    rec = w3.eth.wait_for_transaction_receipt(tx_hash)
    if not rec.get("status"):
        print("deploy reverted")
        return 1
    vault = rec.contractAddress
    print(f"vault deployed: {vault}")

    # relayer cap: owner may also raise/lower it later via setDailyLimit
    v = w3.eth.contract(address=vault, abi=VAULT_ABI)
    nonce = w3.eth.get_transaction_count(owner, "pending")
    tx = v.functions.setDailyLimit(daily_micro).build_transaction(
        {"from": owner, "nonce": nonce, "chainId": w3.eth.chain_id, **_fee_params(w3)}
    )
    signed = owner_acct.sign_transaction(tx)
    w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))

    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8") if env.exists() else ""
    if "VAULT_ADDRESS=" in lines:
        lines = "\n".join(
            ln for ln in lines.splitlines() if not ln.startswith("VAULT_ADDRESS=")
        ) + "\n"
    lines += f"VAULT_ADDRESS={vault}\n"
    env.write_text(lines, encoding="utf-8")
    print("VAULT_ADDRESS appended to .env")

    print()
    print("Next steps:")
    print("  1. Fund the vault: send USDC from the OLD hot wallet to", vault)
    print("  2. Point /deposit at the vault address (deposit_address in ledger/web)")
    print("  3. Set relayer daily cap: call setDailyLimit (owner)")
    print("  4. Verify: dashboard solvency shows reserves_source=vault")
    return 0


def re_fullmatch_64(key: str) -> bool:
    import re

    return bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", key))


if __name__ == "__main__":
    raise SystemExit(main())
