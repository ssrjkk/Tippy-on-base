"""x402 demo script — the on-chain payment handshake, step by step.

For the Loom demo / reviewers: shows exactly how an AI agent pays for a
paywall item (or tips a user) with USDC on Base using x402.

Usage:
    python scripts/x402_demo.py paywall <item_id> --base https://your-host
    python scripts/x402_demo.py tip <username|tg_id> 5 --base https://your-host

It performs leg 1 (invoice) live against your deployed web server. Leg 2
(signing + broadcasting the USDC transfer) needs the paying wallet's key, so
the script prints the exact payload and the command to broadcast it — safe to
run without secrets.
"""

import argparse
import json
import sys

import requests

USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _print_step(title: str, body: str = "") -> None:
    print(f"\n{'=' * 64}\n{title}\n{'=' * 64}")
    if body:
        print(body)


def _post(base: str, path: str, headers: dict | None = None) -> requests.Response:
    url = f"{base}{path}"
    _print_step(f"POST {url}", f"headers: {headers or {}}")
    r = requests.post(url, headers=headers, timeout=30)
    print(f"-> HTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(r.text[:500])
    return r


def paywall(base: str, item_id: int, amount: str) -> None:
    path = f"/api/x402/paywall?item={item_id}&amount={amount}"
    _print_step(
        "LEG 1 — request the invoice (no payment header yet)",
        "An agent asks for the price. The server answers 402 + x-402-* invoice headers.",
    )
    r1 = _post(base, path)
    if r1.status_code != 402:
        sys.exit(1)
    invoice = r1.headers
    _print_step(
        "LEG 1 RESULT — invoice issued",
        "x-402-recipient:  " + invoice.get("x-402-recipient", "")
        + "\nx-402-amount:     " + invoice.get("x-402-amount", "")
        + "\nx-402-expires-at:" + invoice.get("x-402-expires-at", ""),
    )
    amount_micro = int(invoice["x-402-amount"])
    pay_to = invoice["x-402-recipient"]
    _print_step(
        "LEG 2 — broadcast the USDC transfer (needs the paying wallet key)",
        "Send USDC on Base from any wallet you control to the invoice recipient.\n"
        "With a local key + cast:  cast send --rpc-url $BASE_RPC_URL --private-key $KEY "
        f"{USDC_ADDRESS} \"transfer({pay_to}, {amount_micro})\"",
    )
    tx_hash = input("\nPaste the tx hash (0x...): ").strip().lower()
    if not tx_hash.startswith("0x"):
        sys.exit(1)
    _print_step(
        "LEG 3 — repeat the request with the payment header",
        f"x-402-payment: {tx_hash}",
    )
    r2 = _post(base, path, headers={"x-402-payment": tx_hash})
    if r2.status_code == 200:
        _print_step("DONE — content unlocked", "The response body contains the item content.")
    else:
        _print_step("NOT CREDITED YET", "If 402: wait ~2s for the receipt to be readable, then retry.")


def tip(base: str, recipient: str, amount: str) -> None:
    path = f"/api/x402/tip?recipient={recipient}&amount={amount}"
    _print_step("LEG 1 — invoice for a tip", "Same handshake, tips go to a Telegram user's balance.")
    r1 = _post(base, path)
    if r1.status_code != 402:
        sys.exit(1)
    invoice = r1.headers
    pay_to = invoice["x-402-recipient"]
    amount_micro = int(invoice["x-402-amount"])
    print(
        "\nPay " + str(round(amount_micro / 1_000_000, 2))
        + " USDC to " + pay_to + " on Base, then repeat with x-402-payment: <tx>."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kind", choices=["paywall", "tip"])
    ap.add_argument("target")
    ap.add_argument("amount", nargs="?", default="5")
    ap.add_argument("--base", required=True, help="public https URL of your deployed web server")
    args = ap.parse_args()
    if args.kind == "paywall":
        paywall(args.base.rstrip("/"), int(args.target), args.amount)
    else:
        tip(args.base.rstrip("/"), args.target, args.amount)


if __name__ == "__main__":
    main()
