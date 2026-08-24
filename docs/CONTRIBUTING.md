# Contributing to Tippy

Thanks for your interest in improving Tippy — a community economy in USDC on
Base (tips, prediction markets, paywalls, AI assistant). Contributions of all
kinds are welcome: code, docs, bug reports, feature ideas.

## How to contribute

1. **Fork** the repo and create a branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```
2. **Make your change.** Keep the existing style:
   - Python 3.12+, type hints, docstrings explaining *why*, not *what*
   - Handlers live in `bot/handlers/` by domain; shared state only via
     `bot/handlers/_common.py`
   - All money math uses integer micro-units or `Decimal` — never floats
3. **Add tests.** Every bug fix or feature needs coverage in `tests/`.
   We test real behavior: real PostgreSQL, real aiogram dispatcher, real
   crypto. Only external networks (Telegram transport, RPC) are mocked.
4. **Run the quality gates locally:**
   ```bash
   docker compose up -d db          # PostgreSQL on localhost:5433
   python -m pytest tests -q        # all tests must pass
   python -m ruff check bot web tests scripts
   ```
5. **Commit** with a clear message (`feat:`, `fix:`, `docs:`, `refactor:`)
   and open a Pull Request against `main`.

## Money-conservation rule

Any change that moves user funds (tips, bets, markets, withdrawals, fees)
must preserve the core invariant: **balances + escrows always equal total
deposits minus sent withdrawals and fees.** If your feature touches money,
add a conservation assertion to its tests — see `tests/test_markets.py`
for examples.

## Security issues

Do **not** open public issues for security vulnerabilities.
Follow [SECURITY.md](SECURITY.md) for responsible disclosure.

## Code of conduct

Be respectful. By participating you agree to abide by
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Questions

- Telegram: [@ssrjkk](https://t.me/ssrjkk)
- X / Twitter: [@ludych1](https://x.com/ludych1)
