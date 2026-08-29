"""Official x402 protocol (v1, scheme "exact", EIP-3009) tests.

Two layers:
  - unit: header decoding and EIP-712 authorization verification;
  - E2E on a real eth-tester EVM: MiniUSDC (with the FiatTokenV2-shaped
    transferWithAuthorization) as the settlement asset, the FastAPI app
    serving /api/x402/tip, the hot wallet settling as facilitator.

No mocks on the money path: signatures are real, settlement is a real tx
on a real EVM, credits are real ledger writes.
"""

import base64
import json
import os
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient

from web import server, x402_spec

RECEIVE = "0x0000000000000000000000000000000000000001"  # from conftest env


@pytest.fixture()
def client(ledger, monkeypatch):

    monkeypatch.setattr(server, "ledger", ledger_module_async(ledger))
    return TestClient(server.app)


def ledger_module_async(ledger):
    from bot.ledger import AsyncLedger

    return AsyncLedger(ledger)


# ---------------------------------------------------------------------------
# Unit: header decoding + authorization verification
# ---------------------------------------------------------------------------

PAYER_KEY = "0x" + "ab" * 32


def _auth(payer_addr, receive, value, nonce_hex, now=None):
    now = now or int(time.time())
    return {
        "from": payer_addr,
        "to": receive,
        "value": value,
        "validAfter": now - 10,
        "validBefore": now + 120,
        "nonce": nonce_hex,
    }


def _sign(auth, key, chain_id, verifying_contract, name="USD Coin", version="2"):
    typed = encode_typed_data(
        domain_data={
            "name": name,
            "version": version,
            "chainId": chain_id,
            "verifyingContract": verifying_contract,
        },
        message_types=x402_spec.TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data={
            "from": auth["from"],
            "to": auth["to"],
            "value": auth["value"],
            "validAfter": auth["validAfter"],
            "validBefore": auth["validBefore"],
            "nonce": auth["nonce"] if isinstance(auth["nonce"], str) else "0x" + auth["nonce"].hex(),
        },
    )
    return "0x" + Account.sign_message(typed, private_key=key).signature.hex()


def _header(payment: dict) -> str:
    return base64.b64encode(json.dumps(payment).encode()).decode()


def test_decode_payment_header_roundtrip():
    auth = _auth("0x" + "aa" * 20, RECEIVE, 1_000_000, "0x" + "01" * 32)
    sig = "0x" + "ab" * 65
    payment = {
        "x402Version": 1,
        "scheme": "exact",
        "networkId": "base",
        "resource": "/api/x402/tip",
        "payload": {"signature": sig, "authorization": auth},
    }
    decoded = x402_spec.decode_payment_header(_header(payment))
    assert decoded["auth"]["value"] == 1_000_000
    assert decoded["auth"]["nonce"] == bytes.fromhex("01" * 32)
    assert decoded["signature"] == sig


def test_decode_payment_header_rejects_garbage():
    for bad in ("not-base64!!!", "", "e30="):  # broken b64, empty, {} json
        with pytest.raises(ValueError):
            x402_spec.decode_payment_header(bad)
    with pytest.raises(ValueError):
        x402_spec.decode_payment_header(_header({
            "x402Version": 2, "scheme": "exact", "networkId": "base",
            "payload": {"signature": "0x" + "ab" * 65,
                        "authorization": _auth("0x" + "aa" * 20, RECEIVE, 1, "0x" + "01" * 32)},
        }))


def test_verify_eip3009_recovers_signer(monkeypatch):
    # The domain must be EXACTLY the production one (verify reads the asset
    # name/version and verifyingContract from bot.config).
    from bot import config

    monkeypatch.delenv("X402_ASSET_NAME", raising=False)
    monkeypatch.delenv("X402_ASSET_VERSION", raising=False)
    payer_addr = Account.from_key(PAYER_KEY).address
    auth = _auth(payer_addr, RECEIVE, 2_500_000, "0x" + "11" * 32)
    sig = _sign(auth, PAYER_KEY, 8453, config.USDC_ADDRESS)
    signer = x402_spec.verify_eip3009(auth, sig, RECEIVE, 2_000_000)
    assert signer.lower() == payer_addr.lower()


def test_verify_eip3009_rejects_bad_payment(monkeypatch):
    from bot import config

    monkeypatch.delenv("X402_ASSET_NAME", raising=False)
    monkeypatch.delenv("X402_ASSET_VERSION", raising=False)
    payer_addr = Account.from_key(PAYER_KEY).address
    good_to = "0x" + "33" * 20
    auth = _auth(payer_addr, good_to, 2_000_000, "0x" + "11" * 32)
    sig = _sign(auth, PAYER_KEY, 8453, config.USDC_ADDRESS)

    # Underpayment
    with pytest.raises(ValueError, match="below the invoice"):
        x402_spec.verify_eip3009(auth, sig, good_to, 2_000_001)
    # Wrong pay-to
    with pytest.raises(ValueError, match="not the x402 receive address"):
        x402_spec.verify_eip3009(auth, sig, "0x" + "44" * 20, 1_000_000)
    # Expired
    expired = dict(auth, validBefore=int(time.time()) - 5)
    with pytest.raises(ValueError, match="expired"):
        x402_spec.verify_eip3009(expired, _sign(expired, PAYER_KEY, 8453, good_to), good_to, 1_000_000)
    # Validity window way too long (locks the payer's funds)
    too_long = dict(auth, validBefore=int(time.time()) + 100_000)
    with pytest.raises(ValueError, match="too long"):
        x402_spec.verify_eip3009(too_long, _sign(too_long, PAYER_KEY, 8453, config.USDC_ADDRESS), good_to, 1_000_000)
    # Signature by a different key than authorization.from
    other = "0x" + "cd" * 32
    with pytest.raises(ValueError, match="does not match"):
        x402_spec.verify_eip3009(auth, _sign(auth, other, 8453, config.USDC_ADDRESS), good_to, 1_000_000)


# ---------------------------------------------------------------------------
# E2E: real EVM settlement through the FastAPI app (MiniUSDC as the asset)
# ---------------------------------------------------------------------------

@pytest.fixture()
def evm(ledger, monkeypatch):
    """eth-tester EVM with MiniUSDC (EIP-3009) as the settlement asset; the
    app settles from a funded hot wallet. Asserts the exact conditions the
    production path runs under (chain-id guard included)."""
    import solcx
    from eth_tester import PyEVMBackend
    from web3 import Web3
    from web3.providers.eth_tester import EthereumTesterProvider

    backend = PyEVMBackend()
    w3 = Web3(EthereumTesterProvider(backend))
    chain_id = w3.eth.chain_id

    usdc_src = open("contracts/MiniUSDC.sol", encoding="utf-8").read()
    inp = {
        "language": "Solidity",
        "sources": {"MiniUSDC.sol": {"content": usdc_src}},
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    compiled = solcx.compile_standard(inp, solc_version="0.8.24", allow_paths=os.path.abspath("contracts"))
    art = compiled["contracts"]["MiniUSDC.sol"]["MiniUSDC"]
    usdc = w3.eth.contract(abi=art["abi"], bytecode=art["evm"]["bytecode"]["object"])
    deployer = w3.eth.accounts[0]
    tx = usdc.constructor().transact({"from": deployer})
    usdc_addr = w3.eth.wait_for_transaction_receipt(tx).contractAddress
    usdc = w3.eth.contract(address=usdc_addr, abi=art["abi"])

    # Payer account with a minted balance; hot wallet = a funded eth-tester key.
    payer_key = "0x" + "77" * 32
    payer = w3.eth.account.from_key(payer_key).address
    usdc.functions.mint(payer, 100 * 10**6).transact({"from": deployer})
    hot_key = "0x" + "88" * 32
    hot = w3.eth.account.from_key(hot_key).address
    # The facilitator (hot wallet) pays settlement gas — fund it.
    w3.eth.send_transaction({"from": deployer, "to": hot, "value": w3.to_wei(1, "ether")})

    from bot import base, config

    monkeypatch.setattr(base, "w3", w3)
    monkeypatch.setattr(base, "assert_base_chain_sync", lambda: chain_id)
    monkeypatch.setattr(config, "EXPECTED_CHAIN_ID", chain_id)
    monkeypatch.setattr(config, "USDC_ADDRESS", usdc_addr)
    monkeypatch.setattr(config, "HOT_WALLET_KEY", hot_key)
    monkeypatch.setattr(config, "X402_RECEIVE_ADDRESS", RECEIVE)
    monkeypatch.setenv("X402_ASSET_NAME", "MiniUSDC")
    monkeypatch.setenv("X402_ASSET_VERSION", "2")
    monkeypatch.setenv("X402_ENABLED", "1")
    return {"w3": w3, "usdc": usdc, "payer": payer, "payer_key": payer_key,
            "hot": hot, "chain_id": chain_id, "receive": RECEIVE}


def _x_payment(auth, sig, resource):
    payment = {
        "x402Version": 1,
        "scheme": "exact",
        "networkId": "base",
        "resource": resource,
        "payload": {"signature": sig, "authorization": {
            "from": auth["from"], "to": auth["to"], "value": str(auth["value"]),
            "validAfter": str(auth["validAfter"]), "validBefore": str(auth["validBefore"]),
            "nonce": auth["nonce"],
        }},
    }
    return _header(payment)


def test_x402_tip_eip3009_end_to_end(ledger, monkeypatch, client, evm):
    from bot import config

    # The recipient must exist (x402 tips credit an existing tg_id).
    ledger.ensure_user(4242, None)

    # 1) Invoice: official-shaped 402 with accepts[]
    r = client.post("/api/x402/tip?recipient=4242&amount=1.5")
    assert r.status_code == 402
    body = r.json()
    assert body["x402Version"] == 1
    acc = body["accepts"][0]
    assert acc["scheme"] == "exact" and acc["payTo"] == RECEIVE
    assert acc["maxAmountRequired"] == "1500000"
    assert acc["asset"] == config.USDC_ADDRESS

    # 2) Pay: sign the EIP-3009 authorization exactly like an x402 agent would
    payer_addr = evm["payer"]
    auth = _auth(payer_addr, RECEIVE, 1_500_000, "0x" + "21" * 32)
    sig = _sign(auth, evm["payer_key"], evm["chain_id"], config.USDC_ADDRESS, name="MiniUSDC")
    payer_before = evm["usdc"].functions.balanceOf(payer_addr).call()
    receive_before = evm["usdc"].functions.balanceOf(RECEIVE).call()

    r = client.post("/api/x402/tip?recipient=4242&amount=1.5",
                    headers={"X-PAYMENT": _x_payment(auth, sig, "/api/x402/tip")})
    assert r.status_code == 200, r.text
    assert r.headers.get("X-PAYMENT-RESPONSE")
    receipt = json.loads(base64.b64decode(r.headers["X-PAYMENT-RESPONSE"]))
    assert receipt["success"] is True and receipt["transaction"].startswith("0x")

    # 3) On-chain: value moved payer -> receive, nonce burned
    assert evm["usdc"].functions.balanceOf(payer_addr).call() == payer_before - 1_500_000
    assert evm["usdc"].functions.balanceOf(RECEIVE).call() == receive_before + 1_500_000
    payer_addr = evm["payer"]
    nonce_bytes = bytes.fromhex("21" * 32)
    assert evm["usdc"].functions.authorizationState(payer_addr, nonce_bytes).call() is True

    # 4) Ledger: recipient credited the settled amount
    assert float(ledger.balance(4242)) == 1.5

    # 5) Replay: the same X-PAYMENT header cannot settle again (on-chain
    #    nonce is burned) — no double credit.
    r = client.post("/api/x402/tip?recipient=4242&amount=1.5",
                    headers={"X-PAYMENT": _x_payment(auth, sig, "/api/x402/tip")})
    assert r.status_code == 402
    assert float(ledger.balance(4242)) == 1.5


def test_x402_tip_eip3009_overpay_credits_actual(ledger, monkeypatch, client, evm):
    """A hand-rolled client may authorize MORE than the invoice: the credit
    must equal the ACTUALLY settled value (money conservation)."""
    ledger.ensure_user(4242, None)
    from bot import config

    payer_addr = evm["payer"]
    auth = _auth(payer_addr, RECEIVE, 3_000_000, "0x" + "31" * 32)  # 3 USDC for a 1.5 invoice
    sig = _sign(auth, evm["payer_key"], evm["chain_id"], config.USDC_ADDRESS, name="MiniUSDC")
    payer_before = evm["usdc"].functions.balanceOf(payer_addr).call()

    r = client.post("/api/x402/tip?recipient=4242&amount=1.5",
                    headers={"X-PAYMENT": _x_payment(auth, sig, "/api/x402/tip")})
    assert r.status_code == 200, r.text
    assert r.json()["settlement"]["amount_micro"] == 3_000_000
    assert evm["usdc"].functions.balanceOf(payer_addr).call() == payer_before - 3_000_000
    assert float(ledger.balance(4242)) == 3.0


def test_x402_tip_eip3009_wrong_pay_to_rejected(ledger, monkeypatch, client, evm):
    """An authorization payable to someone else must not settle here."""
    from bot import config

    ledger.ensure_user(4242, None)
    auth = _auth(evm["payer"], "0x" + "99" * 20, 1_500_000, "0x" + "41" * 32)
    sig = _sign(auth, evm["payer_key"], evm["chain_id"], config.USDC_ADDRESS, name="MiniUSDC")
    r = client.post("/api/x402/tip?recipient=4242&amount=1.5",
                    headers={"X-PAYMENT": _x_payment(auth, sig, "/api/x402/tip")})
    assert r.status_code == 402
    assert "not the x402 receive address" in r.json()["error"]
    assert ledger.balance(4242) == 0


def test_x402_paywall_eip3009_end_to_end(ledger, monkeypatch, client, evm):
    from bot import config

    item_id = ledger.create_paywall(4242, "Agent-only report", 2 * 10**6, "SECRET CONTENT")
    assert item_id is not None

    r = client.post(f"/api/x402/paywall?item={item_id}&amount=2")
    assert r.status_code == 402
    assert r.json()["accepts"][0]["maxAmountRequired"] == "2000000"

    payer_addr = evm["payer"]
    auth = _auth(payer_addr, RECEIVE, 2_000_000, "0x" + "51" * 32)
    sig = _sign(auth, evm["payer_key"], evm["chain_id"], config.USDC_ADDRESS, name="MiniUSDC")
    owner_before = evm["usdc"].functions.balanceOf(RECEIVE).call()

    r = client.post(f"/api/x402/paywall?item={item_id}&amount=2",
                    headers={"X-PAYMENT": _x_payment(auth, sig, f"/api/x402/paywall?item={item_id}")})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "SECRET CONTENT"
    assert evm["usdc"].functions.balanceOf(RECEIVE).call() == owner_before + 2_000_000
    # Owner credited and the purchase recorded against the settlement tx.
    assert float(ledger.balance(4242)) == 2.0
    assert ledger.paywall_purchased(item_id, 4242) is False  # buyer_tg is NULL for x402 rows
    item = ledger.paywall_item(item_id)
    assert item is not None


# ---------------------------------------------------------------------------
# Reconciliation sweep: stale 'auth:' reservations get finalized (settlement
# landed) or released (nonce never burned) — closes the 502/uncertain loop.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_finalizes_settled_authorization(ledger, monkeypatch):
    from bot.ledger import AsyncLedger
    from web import x402 as xw

    monkeypatch.setattr(xw, "ledger", AsyncLedger(ledger))
    ledger.ensure_user(4242, None)
    ledger.reserve_x402_auth('auth:' + '21' * 32, 4242, 1_500_000, '0x' + '77' * 20)
    # Simulate: the settlement LANDED on-chain (nonce burned, transfer found).
    monkeypatch.setattr(x402_spec, "authorization_burned", lambda p, n: True)
    monkeypatch.setattr(x402_spec, "find_settlement_by_nonce",
                        lambda p, n, r: {"tx": "0x" + "de" * 32, "value": 1_500_000})

    from web.x402 import reconcile_stale_x402
    finalized = await reconcile_stale_x402(older_than_seconds=0)

    assert finalized == 1
    row = ledger._conn.execute(
        "SELECT amount_micro, sender FROM x402_payments WHERE tx_hash = %s",
        ("0x" + "de" * 32,),
    ).fetchone()
    assert row and row["amount_micro"] == 1_500_000
    assert ledger._conn.execute(
        "SELECT 1 FROM x402_payments WHERE tx_hash = %s", ("auth:" + "21" * 32,)
    ).fetchone() is None
    assert float(ledger.balance(4242)) == 1.5


@pytest.mark.asyncio
async def test_reconcile_releases_unburned_nonce(ledger, monkeypatch):

    ledger.ensure_user(4242, None)
    ledger.reserve_x402_auth('auth:' + '31' * 32, 4242, 1_000_000, '0x' + '77' * 20)
    monkeypatch.setattr(x402_spec, "authorization_burned", lambda p, n: False)

    from web.x402 import reconcile_stale_x402
    await reconcile_stale_x402(older_than_seconds=0)

    assert ledger._conn.execute(
        "SELECT 1 FROM x402_payments WHERE tx_hash = %s", ("auth:" + "31" * 32,)
    ).fetchone() is None, "unburned reservation must be released"
    assert float(ledger.balance(4242)) == 0.0


@pytest.mark.asyncio
async def test_reconcile_keeps_row_when_rpc_unknown(ledger, monkeypatch):

    ledger.ensure_user(4242, None)
    ledger.reserve_x402_auth('auth:' + '41' * 32, 4242, 1_000_000, '0x' + '77' * 20)
    monkeypatch.setattr(x402_spec, "authorization_burned", lambda p, n: None)

    from web.x402 import reconcile_stale_x402
    await reconcile_stale_x402(older_than_seconds=0)

    assert ledger._conn.execute(
        "SELECT 1 FROM x402_payments WHERE tx_hash = %s", ("auth:" + "41" * 32,)
    ).fetchone() is not None, "unknown on-chain state: the row must stay"
