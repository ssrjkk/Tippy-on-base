"""EAS (Ethereum Attestation Service) on-chain attestation for agent actions.

Every agent action produces a public, immutable attestation on Base.
This creates an audit trail = trust + grant bait.

EAS contract on Base mainnet: 0xC2679fBD36d5E93C340e118209b9F0D949c0b167
Schema (simplified): action_type(string), market_id(uint256), amount_usdc(uint256),
                      confidence(uint8), reasoning_hash(bytes32), timestamp(uint256)

Requires: web3.py (already in requirements.txt)
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass

from web3 import Web3
from eth_abi import encode as abi_encode

# EAS contract on Base mainnet
EAS_ADDRESS = "0xC2679fBD36d5E93C340e118209b9F0D949c0b167"

# Minimal ABI for EAS attestation
EAS_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "recipient", "type": "address"},
                    {"name": "expirationTime", "type": "uint256"},
                    {"name": "revocable", "type": "bool"},
                    {"name": "refUUID", "type": "bytes32"},
                    {"name": "schema", "type": "bytes32"},
                    {"name": "data", "type": "bytes"},
                ],
                "name": "request",
                "type": "tuple",
            }
        ],
        "name": "attest",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

# Schema for Tippy agent actions (32 bytes = keccak256 hash)
# Schema: "string action_type,uint256 market_id,uint256 amount_micro,uint8 confidence,bytes32 reasoning_hash,uint256 ts"
SCHEMA_STR = "string action_type,uint256 market_id,uint256 amount_micro,uint8 confidence,bytes32 reasoning_hash,uint256 ts"
SCHEMA_UUID = Web3.keccak(text=SCHEMA_STR)


@dataclass
class AttestationData:
    action_type: str  # "create_market", "place_bet", "sell_signal"
    market_id: int
    amount_micro: int
    confidence: int  # 0-100
    reasoning: str

    @property
    def reasoning_hash(self) -> bytes:
        return Web3.keccak(text=self.reasoning[:200])

    def encode_data(self) -> bytes:
        """ABI-encode the attestation data."""
        return abi_encode(
            ["string", "uint256", "uint256", "uint8", "bytes32", "uint256"],
            [
                self.action_type,
                self.market_id,
                self.amount_micro,
                self.confidence,
                self.reasoning_hash,
                int(time.time()),
            ],
        )


def _get_w3():
    rpc_url = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
    return Web3(Web3.HTTPProvider(rpc_url))


def _get_attester_key() -> str | None:
    """Return the private key for attestation signing.

    Uses WALLET_ENC_KEY env var (encrypted). For demo, accepts raw key.
    In production, use CDP MPC wallet or HSM.
    """
    return os.environ.get("AGENT_EAS_KEY") or os.environ.get("WALLET_ENC_KEY")


def attest_action(data: AttestationData) -> str | None:
    """Submit an EAS attestation on Base. Returns tx hash or None on failure.

    For the demo, this logs locally and prints the attestation data.
    Production would submit on-chain via the attester key.
    """
    w3 = _get_w3()
    key = _get_attester_key()

    if not key:
        # No key available — log locally
        _log_local(data)
        return None

    try:
        eas = w3.eth.contract(
            address=Web3.to_checksum_address(EAS_ADDRESS),
            abi=EAS_ABI,
        )
        attester = w3.eth.account.from_key(key)

        request = (
            Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),  # recipient
            0,  # expirationTime (0 = no expiry)
            True,  # revocable
            b"\x00" * 32,  # refUUID
            SCHEMA_UUID,
            data.encode_data(),
        )

        tx = eas.functions.attest(request).build_transaction({
            "from": attester.address,
            "nonce": w3.eth.get_transaction_count(attester.address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": 8453,  # Base mainnet
        })

        signed = w3.eth.account.sign_transaction(tx, key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt["status"] == 1:
            return tx_hash.hex()
        return None

    except Exception:
        _log_local(data)
        return None


def _log_local(data: AttestationData) -> None:
    """Fallback: log attestation to local JSONL file."""
    import pathlib
    log_file = pathlib.Path("agent_attestations.jsonl")
    entry = {
        "ts": time.time(),
        "action_type": data.action_type,
        "market_id": data.market_id,
        "amount_micro": data.amount_micro,
        "confidence": data.confidence,
        "reasoning_hash": data.reasoning_hash.hex(),
        "schema_uuid": SCHEMA_UUID.hex(),
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
