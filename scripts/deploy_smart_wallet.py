"""Deploy the P2 Smart Wallet stack (ERC-4337) on Base (mainnet or Sepolia):
    - VerifyingPaymaster   (gasless sponsorship, relayer-signed)
    - SmartAccountFactory  (CREATE2, init code = SmartAccount creation bytecode)

Usage:
    python scripts/deploy_smart_wallet.py --dry-run
    python scripts/deploy_smart_wallet.py
    python scripts/deploy_smart_wallet.py --wire-env

Environment:
    BASE_RPC_URL        RPC (defaults to sepolia.base.org)
    EXPECTED_CHAIN_ID   chain guard (default 84532 for Sepolia)
    HOT_WALLET_KEY      deployer + paymaster owner (relayer)
    USDC_ADDRESS        network USDC (defaults to Base Sepolia)
    SMART_WALLET_ENTRYPOINT  EntryPoint v0.6 (defaults to Base mainnet/Sepolia)

Steps:
    1. Compile VerifyingPaymaster, SmartAccountFactory, SmartAccount (solcx).
    2. Deploy VerifyingPaymaster(owner=deployer, usdc, gasChargeMicro) — the
       owner is the bot relayer key that signs UserOperations.
    3. Deploy SmartAccountFactory(entrypoint, smartAccountCreationBytecode) —
       the creation bytecode of SmartAccount becomes each account's init code
       (running it runs the parameterless constructor and RETURNs the runtime
       code that CREATE2 keeps as the deployed account).
    4. Optional --wire-env writes SMART_WALLET_* keys into .env.

NOTE: P2 is gated behind SMART_WALLET_ENABLED=1; keep it 0 until the stack is
      deployed and smoke-tested. SmartAccount has no deployed-bytecode cost to
      deploy itself (only the factory already carries its runtime code).
"""
import argparse
import os
import sys

from deploy_create2_factory import (  # reusable: cfg, ROOT, ENV_FILE, _compile
    ENV_FILE,
    ROOT,
    _compile,
    cfg,
)

EntryPoint_DEFAULT = "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789"  # v0.6
RPC_DEFAULT = "https://sepolia.base.org"
CHAIN_DEFAULT = 84532
USDC_DEFAULT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Base Sepolia USDC


def compile_contracts():
    paymaster = _compile("VerifyingPaymaster.sol",
                         (ROOT / "contracts" / "VerifyingPaymaster.sol").read_text(encoding="utf-8"),
                         "VerifyingPaymaster")
    factory = _compile("SmartAccountFactory.sol",
                       (ROOT / "contracts" / "SmartAccountFactory.sol").read_text(encoding="utf-8"),
                       "SmartAccountFactory")
    account = _compile("SmartAccount.sol",
                       (ROOT / "contracts" / "SmartAccount.sol").read_text(encoding="utf-8"),
                       "SmartAccount")
    return paymaster, factory, account


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default=None)
    parser.add_argument("--chain", type=int, default=None)
    parser.add_argument("--usdc", default=None)
    parser.add_argument("--gas-charge-micro", type=int, default=0)
    parser.add_argument("--owner", default=None,
                        help="paymaster owner/relayer key address (default: deployer). "
                             "In production this is the relayer key that signs UserOps; "
                             "a Safe multisig cannot sign per-UserOp sponsorship, so keep "
                             "it as an operator key and secure it via gas caps.")
    parser.add_argument("--fund-paymaster", type=float, default=0.0, metavar="ETH",
                        help="extra step: deposit ETH into the EntryPoint under the "
                             "paymaster address after deploy (needed on mainnet to sponsor gas)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wire-env", action="store_true")
    args = parser.parse_args()

    paymaster_art, factory_art, account_art = compile_contracts()

    rpc = args.rpc or cfg("BASE_RPC_URL") or RPC_DEFAULT
    key = cfg("HOT_WALLET_KEY") or os.environ.get("DEPLOYER_KEY")
    if not key:
        print("ERROR: HOT_WALLET_KEY not set", file=sys.stderr)
        return 1
    usdc = (args.usdc or cfg("USDC_ADDRESS") or USDC_DEFAULT).strip()
    entrypoint = (cfg("SMART_WALLET_ENTRYPOINT") or EntryPoint_DEFAULT).strip()
    expected_chain = args.chain or int(cfg("EXPECTED_CHAIN_ID", str(CHAIN_DEFAULT)) or CHAIN_DEFAULT)

    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print("ERROR: RPC unreachable:", rpc)
        return 1
    chain_id = w3.eth.chain_id
    if chain_id != expected_chain:
        print(f"ERROR: RPC on chain {chain_id}, expected {expected_chain}. Refusing to deploy.")
        return 1

    acct = w3.eth.account.from_key(key)
    deployer = Web3.to_checksum_address(acct.address)
    owner = Web3.to_checksum_address(args.owner) if args.owner else deployer
    usdc = Web3.to_checksum_address(usdc)
    entrypoint = Web3.to_checksum_address(entrypoint)

    print(f"[deploy] chain={chain_id} deployer={deployer} owner={owner} usdc={usdc} entrypoint={entrypoint}")
    if w3.eth.get_code(usdc) == b"":
        print(f"ERROR: no code at USDC {usdc} on chain {chain_id} (wrong network?)")
        return 1
    if chain_id in (8453, 84532) and w3.eth.get_code(entrypoint) == b"":
        print(f"WARNING: no code at EntryPoint {entrypoint}; double-check it is the v0.6 deployment")

    bal = w3.eth.get_balance(deployer)
    print(f"[deploy] balance={bal / 1e18:.6f} ETH nonce={w3.eth.get_transaction_count(deployer)}")

    def _send_contract(abi, bin, *ctor_args, label: str):
        c = w3.eth.contract(abi=abi, bytecode=bin)
        ctor = c.constructor(*ctor_args)
        gas_est = ctor.estimate_gas({"from": deployer})
        base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
        priority = max(w3.eth.gas_price - base_fee, 10**6)
        max_fee = base_fee * 2 + priority
        tx = ctor.build_transaction({
            "chainId": chain_id,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": int(gas_est * 1.2),
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
        })
        signed = acct.sign_transaction(tx)
        cost = tx["gas"] * max_fee
        print(f"[deploy] {label}: gas={tx['gas']:,} max_fee={max_fee / 1e9:.4f} gwei cost~{cost / 1e18:.8f} ETH")
        if args.dry_run:
            print(f"[dry-run] would broadcast {label} (signed, not sent)")
            return None
        if bal < cost:
            print(f"INSUFFICIENT GAS: need ~{cost / 1e18:.8f} ETH on {deployer}")
            sys.exit(2)
        sent = w3.eth.send_raw_transaction(signed.raw_transaction)
        rc = w3.eth.wait_for_transaction_receipt(sent, timeout=300, poll_latency=2)
        if rc.status != 1:
            raise SystemExit(f"TX REVERTED: {sent.hex()}")
        print(f"[deploy] {label} broadcast {sent.hex()[:20]}… block={rc.blockNumber} gas={rc.gasUsed}")
        return rc.contractAddress

    # Deploy paymaster first (factory does not depend on it).
    # Init code for each account = SmartAccount CREATION bytecode: running it
    # runs the (parameterless) constructor and RETURNs the runtime code that
    # CREATE2 keeps as the deployed account. Using the raw runtime bytecode
    # here is WRONG — executing it as init code ends in REVERT and returns 0.
    account_init = "0x" + account_art["bin"].lstrip("0x")
    pm_addr = _send_contract(
        paymaster_art["abi"], paymaster_art["bin"],
        owner, usdc, args.gas_charge_micro, label="VerifyingPaymaster",
    )
    if args.dry_run:
        print("DRY-RUN OK")
        return 0
    assert pm_addr, "paymaster deploy failed"

    fac_addr = _send_contract(
        factory_art["abi"], factory_art["bin"],
        entrypoint, account_init, label="SmartAccountFactory",
    )
    assert fac_addr, "factory deploy failed"

    # On-chain sanity checks
    fac = w3.eth.contract(address=fac_addr, abi=factory_art["abi"])
    pm = w3.eth.contract(address=pm_addr, abi=paymaster_art["abi"])
    try:
        pred = fac.functions.getAddress(123456, owner).call()
        print(f"[check] getAddress(123456, owner) pred: {pred}")
        entrypoint_ok = fac.functions.entryPoint().call().lower() == entrypoint.lower()
        pm_owner_ok = pm.functions.owner().call().lower() == owner.lower()
        print(f"[check] factory.entryPoint: {'OK' if entrypoint_ok else 'FAIL'}")
        print(f"[check] paymaster.owner==owner: {'OK' if pm_owner_ok else 'FAIL'}")
    except Exception as e:  # noqa: BLE001
        print(f"[check] on-chain sanity read failed: {e}")

    print()
    print(f"VerifyingPaymaster : {pm_addr}")
    print(f"SmartAccountFactory: {fac_addr}")
    print(f"EntryPoint         : {entrypoint}")
    print(f"SmartAccount init/code len: {len(account_init) // 2 - 1} bytes")

    # Optional: fund the paymaster's EntryPoint deposit so it can actually
    # sponsor gas. Without this the gasless flow fails with EP-04/insufficient
    # paymaster balance. Only meaningful on mainnet (Sepolia is free-ish).
    if args.fund_paymaster > 0:
        _fund_paymaster(w3, acct, pm_addr, entrypoint, args.fund_paymaster, chain_id)

    if args.wire_env:
        _wire_env(fac_addr, pm_addr, entrypoint)

    return 0


def _fund_paymaster(w3, acct, pm_addr, entrypoint, eth_amount: float, chain_id: int) -> None:
    """Deposit ETH into the EntryPoint under the paymaster address.

    EP.depositTo(paymaster) credits the paymaster's deposit, which the
    EntryPoint draws on to pay postOp gas. Owner/anyone can top it up.
    """
    ep = w3.eth.contract(address=entrypoint, abi=[{
        "inputs": [{"name": "account", "type": "address"}],
        "name": "depositTo", "outputs": [], "stateMutability": "payable", "type": "function",
    }])
    value = int(eth_amount * 1e18)
    deployer = acct.address
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    priority = max(w3.eth.gas_price - base_fee, 10 ** 6)
    tx = ep.functions.depositTo(pm_addr).build_transaction({
        "from": deployer, "chainId": chain_id, "value": value,
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": 150_000, "maxFeePerGas": base_fee * 2 + priority,
        "maxPriorityFeePerGas": priority,
    })
    signed = acct.sign_transaction(tx)
    sent = w3.eth.send_raw_transaction(signed.raw_transaction)
    rc = w3.eth.wait_for_transaction_receipt(sent, timeout=300, poll_latency=2)
    if rc.status != 1:
        raise SystemExit(f"paymaster fund TX REVERTED: {sent.hex()}")
    print(f"[fund] paymaster deposit +{eth_amount} ETH tx={sent.hex()[:20]}… block={rc.blockNumber}")


def _wire_env(factory: str, paymaster: str, entrypoint: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    writes = {
        "SMART_WALLET_FACTORY_ADDRESS": factory,
        "SMART_WALLET_PAYMASTER_ADDRESS": paymaster,
        "SMART_WALLET_ENTRYPOINT": entrypoint,
        # Relayer defaults to HOT_WALLET_KEY; limits mirror the paymaster caps.
        "SMART_WALLET_GAS_LIMIT_USD": "0.50",
        "SMART_WALLET_GAS_GLOBAL_USD": "5.00",
    }
    out = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip()
        if key in writes:
            out.append(f"{key}={writes.pop(key)}")
        else:
            out.append(ln)
    for k, v in writes.items():
        out.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[wire] wrote SMART_WALLET_* keys into {ENV_FILE}")


if __name__ == "__main__":
    sys.exit(main())
