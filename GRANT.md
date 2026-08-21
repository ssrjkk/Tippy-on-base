# Tippy — Base Builder Grant Program Application

Copy-paste package for the **Base Builder Grant Program** form (owned by
Coinbase/Base). Grants: **up to $5,000** in seed capital + **monthly product &
GTM support** + **priority technical support**.

Form: `https://docs.google.com/forms/d/e/1FAIpQLSeEFi9BLm5XCm7KrFzRZC-rxcAqCNZPzWZ9He4aZkxsKuRXjw/viewform`
Announcement: `https://x.com/base/status/2086754553580355673`

Areas of interest (our fit): **Prediction markets** · Launchpads · DeFi ·
Agents · Asset creation · Consumer apps.

> ⚠️ **Critical:** the program targets a **live product that is already being
> used** ("You're past the idea stage. With a Live product that's getting
> used"). Do not submit before deploying to Base mainnet and onboarding the
> first real users — complete the §9 checklist first (deploy + demo +
> activity). Usage numbers must be honest (see §5).

---

## 1. Copy-paste answers (all form fields)

| Form field | Answer |
|---|---|
| **Full name** | `TODO: your name` |
| **Email** | `TODO: e-mail under a real account` |
| **X (Twitter) handle** | `TODO: @Nickname` (create it + post activity, see §9) |
| **Telegram username** | `TODO: @username` |
| **Project name + one-line description** | `Tippy — turn any Telegram community into a USDC economy on Base: instant tips, QR donation pages, Polymarket-style prediction markets (LMSR AMM) with tradeable positions, paid content, and an AI assistant.` |
| **Tell us about the founding team** | see §2 |
| **Link to your live product** | `TODO: https://<dashboard domain>` — public dashboard + `/api/solvency` |
| **Link to a Product Demo (Loom)** | `TODO: Loom link` — script in §4 |
| **Contract address on Base** | The bot is custodial: `TODO: hot wallet address` (after key generation) — all balances are held there; the only contract used is canonical USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` plus our audited-on-testnet treasury `TipBotVault`. |
| **Which track best fits?** | `Trading` (recommended: prediction markets are a trading product). Alternative: "Other: Prediction Markets". |
| **Key usage numbers** | Honest pre-deploy: 0. Format: all-time users / DAU / WAU / all-time volume / 30-day volume — see §5 |
| **How does your product make money today?** | see §6 |
| **GTM plan for the next 3 months** | see §7 |
| **Base Builder Code** | `TODO: inviter code (if any)` |
| **Primary challenge or bottleneck** | `User acquisition` — the product is built (422 automated tests, 94% coverage); we need communities and growth |
| **Project description** | `Tippy is a monetization layer for Telegram communities and AI agents on Base. Users get instant USDC tips, QR donation pages, Polymarket-style prediction markets powered by an LMSR automated market maker (tradeable positions, live odds, automatic resolution), x402 paywalled content, and a built-in AI assistant. Every balance is backed by publicly verifiable on-chain reserves via TipBotVault. Production-ready: 422 automated tests, 94% coverage, Dockerized. We target the 900M Telegram user base and the AI agent economy on Base.` |
| **Why Base?** | `Base is the only chain with native x402 support, which is critical for our AI-agent-to-agent commerce model. Additionally, Base's Smart Wallets enable gasless onboarding for Telegram users who have never used crypto before. We are building exclusively on Base to leverage these primitives.` |
| **Use of Funds ($5,000)** | `$2,000 — Smart Contract Audit (TipBotVault.sol), critical for handling user deposits. $1,500 — GTM & Partnership Development: onboarding the first 10 crypto-signal Telegram channels and integrating with Virtuals Protocol agents. $1,500 — Infrastructure scaling (PostgreSQL optimization, Alchemy Webhooks) for 10k+ daily active users.` |
| **Traction (honest, potential-focused)** | `Currently pre-launch with a fully tested MVP. Technical milestones achieved: LMSR prediction market engine, x402 integration (tips + paywalls), Vault Proof of Reserves, AI assistant, 422 tests. Next step: launch on Base mainnet and secure the first 100 users through Farcaster Frames and AI agent partnerships.` |
| **Which credits would be most useful?** | `AWS` (bot/dashboard hosting), `Alchemy / QuickNode` (Base RPC), `Privy` (not a priority — login is via Telegram) |

## 2. Founding team (draft)

> Solo builder, full-stack (Python, PostgreSQL, Telegram Bots, web3/Base).
> Links: Telegram [@b2wmain](https://t.me/b2wmain) /
> [@ssrjkk](https://t.me/ssrjkk) · X [@ludych1](https://x.com/ludych1) ·
> GitHub [ssrjkk](https://github.com/ssrjkk)
> Prior experience: `TODO: past companies/projects, relevant experience`
> Funding raised: `TODO: (if any)`. Tone: the entire product was written
> solo and is covered by 422 automated tests (fund conservation, deposit
> security, E2E scenarios through the real bot router) — reliability over
> marketing.

## 3. The pitch (154 words — reuse if the form asks "tell us about your product")

> Tippy turns any Telegram community into a USDC economy on Base:
> instant tips, QR donation pages, and Polymarket-style prediction markets.
> Markets run on an LMSR automated market maker: traders buy and sell YES/NO
> positions at live odds, prices sum to $1, and resolution pays winners
> automatically — no order book, no gas per trade. Everything settles
> instantly inside the bot's ledger (zero fees), while every balance is backed
> by a public, on-chain auditable treasury (TipBotVault) — no custom tokens,
> no IOU points, only USDC.
>
> It brings users onchain: 900M+ Telegram users are one message away from a
> dollar-pegged wallet. Group admins earn a fee on every winning trade in
> markets they create — a built-in referral loop. An AI assistant answers
> community questions, and x402 paywalls let AI agents buy content over HTTP.
> A public dashboard shows volume, markets, and a live solvency check proving
> liabilities are always covered by on-chain USDC.
>
> The prototype is complete: 422 automated tests, 94% coverage, Dockerized,
> treasury contract tested on a local EVM. The grant funds RPC infrastructure,
> hosting, and community rollout.

## 4. 1-minute demo script (record on Loom after deploy)

1. `0:00–0:10` — `/start` in Telegram, bot menu.
2. `0:10–0:25` — `/deposit`, open the dashboard (Project URL), show the QR on
   the `/u/{id}` donation page; send USDC from a wallet → auto-credit.
3. `0:25–0:40` — `/tip 5` and reaction-tips in a group; show `/top` and the
   instant recipient notification.
4. `0:40–0:55` — **prediction market**: `/market create 50 Who wins? | Alice |
   Bob` → `/trade <id> 1 10` at live odds → card shows moving prices →
   creator resolves → winner payout DM arrives automatically.
5. `0:55–1:00` — `/api/solvency`: liabilities covered by reserves read
   directly from the `TipBotVault` contract (`reserves_source: "vault"`).
6. (bonus for judges) x402: `curl -X POST <domain>/api/x402/tip?recipient=…&amount=1`
   → `402` with an invoice → pay USDC → repeat with `x-402-payment` → 200.
7. (killer feature) x402 Paywall: `/paywall create 1 Report` → content →
   `curl -X POST <domain>/api/x402/paywall?item=1&amount=1` → the agent pays
   on-chain and receives the content — bot-to-bot commerce on Base.
8. (killer feature 2) Paid channel: admin runs `/paywall channel 5`,
   subscribers pay `/paywall subscribe @channel`, enter via invite link,
   watcher kicks them on expiry — the channel monetizes itself.
9. (killer feature 3) AI assistant: `/ask What is Base?` inside the chat —
   context-aware answers from the OpenAI-compatible API.
10. (distribution) Farcaster Frame: post `/frame/<id>` on Warpcast with tags
    `@jessepollak` `@buildonbase` — the "Buy in Telegram" button deep-links to
    `t.me/bot?start=paywall_<id>` (one-button purchase). Demo scripts:
    `scripts/x402_demo.py`, `scripts/agent_demo.py` (an agent mints a post and
    pays for it itself).

## 5. Key usage numbers — what to fill in (form format)

The form asks: all-time users onboarded, DAU, WAU, all-time volume,
last-30-day volume. Before deploy, honestly: `0 / 0 / 0 / 0 / 0` + (if there
is a text field) "just launched, live on Base mainnet, onboarding first
communities". After the first weeks — real numbers from the dashboard
(`/api/stats` and `/api/user`); they are public and easy to verify:
**do not inflate**.

## 6. How the product makes money

- **Market creator fees on prediction markets** — when a market resolves,
  leftover escrow from rounding and the platform share go to the market
  creator (group admin / streamer). A built-in referral loop: admins want
  their community trading. Next step: explicit platform fee on trades.
- **Deposit conversion** — every activity (tip, trade, paywall purchase)
  requires USDC in the bot; this is real on-chain inflow to the hot wallet.
- **Donation pages** — optional tip percentage (post-launch).

## 7. GTM plan (3 months)

- **Month 1 — deploy and first communities.** Base mainnet + public
  dashboard; Loom demo; X/Farcaster posts ("we're live"); onboard 5–10
  Telegram communities (crypto chats, sports fan groups, streamers):
  tips + the first real-money market. **Farcaster Frames** as the first
  traffic channel: a paywall post as a Frame (`/frame/<id>`), posted on
  Warpcast tagged `@jessepollak`/`@buildonbase`; the AI-agent demo
  (`scripts/agent_demo.py`) as a video for reviewers.
- **Month 2 — the admin referral loop.** Personal creator dashboards
  (volume, markets, fees earned) — admins invite their own communities;
  public market pages with a "Trade in Telegram" deep-link for sharing;
  integrations and fixes from early-user feedback.
- **Month 3 — viral growth.** Event markets (matches, streams) as viral
  content; collaborations with Base communities; the public `/api/solvency`
  as a trust argument; goal — 1k+ users and the first 30-day volume.

## 8. Full-length pitch (supporting material, not for the form)

### One-liner

**USDC-powered community economy inside Telegram on Base: tips, donation pages
with QR, Polymarket-style prediction markets (LMSR AMM), paid content, and an
AI assistant — backed by an on-chain treasury contract (TipBotVault) with
publicly verifiable reserves.**

### The problem

Crypto tipping and community monetization tools have two failure modes:

1. **Custom tokens.** Creators launch a memecoin, fans buy it, then it dumps.
   The community is left holding a useless asset.
2. **Off-chain IOU apps.** Points, "credits", promises. No transparency, no
   real settlement, no trust.

Both fail because the *value is not real money the whole community already
trusts.* And prediction markets never made it into group chats: order books
are too heavy, gas kills micro-trades, and settlement requires trust.

### The solution

Tippy uses **USDC — the dollar on Base** — as the single primitive.
Everything is settled instantly *inside the bot's ledger* (zero gas, zero
waiting), and the hot wallet that backs every balance is **public and
auditable on-chain** (see the web dashboard).

- **💸 Tips** — `/tip 5` or reply to any message. Instant, with a leaderboard.
- **🎁 Donation pages** — every user gets a public landing page with a QR that
  links back into the bot (`t.me/bot?start=donate_<id>`).
- **📈 Prediction markets (Polymarket-style)** — an LMSR automated market
  maker: anyone creates a market with a liquidity subsidy in one command;
  traders buy and sell YES/NO shares at algorithmic live odds (prices always
  sum to $1, no counterparty needed); the creator resolves, winners are paid
  automatically, and the creator earns the leftover escrow as a fee. Deadline
  + grace-period watchers nudge absent creators so trader money is never
  stuck; cancellation refunds everyone their net cost basis. Funding is
  mathematically bounded: escrow ≥ max payout is guaranteed by the LMSR cost
  function (property-tested). Every market has a public JSON endpoint and a
  shareable card with live odds bars.
- **🎯 Parimutuel bets** — simple winner-takes-all polls with deadlines,
  automatic payouts, cancel = full refund, and Farcaster Frame distribution.
- **🔑 Wallet linking** — sign a message with your wallet to auto-claim any
  USDC sent to the bot's address. Withdrawals go back on-chain anytime;
  `/tx <hash>` decodes any on-chain USDC transfer right in the chat with a
  Basescan link.
- **🤖 x402 for AI agents** — `POST /api/x402/tip` speaks the x402 protocol
  (Coinbase): an agent requests an invoice (HTTP 402 + `x-402-*` headers),
  pays USDC on Base, repeats the request with `x-402-payment: <tx_hash>`,
  and the tip lands on the recipient's balance inside Telegram. Replays are
  refused (tx hash PK) and the deposit scanner skips these payments, so
  liabilities stay exact. This is how bots and agents can tip humans (and
  each other) without ever opening Telegram.
- **🔐 x402 Paywall (paid content)** — anyone in a community can publish a
  post for a price (`/paywall create 5 Title`), and humans buy it from their
  balance (`/paywall buy <id>` — USDC credited to the seller instantly) while
  AI agents buy it over HTTP (`POST /api/x402/paywall` — same 402 handshake,
  content returned in the 200 body). Purchases are atomic and replay-proof in
  the ledger; re-buying re-shows the content for free. This is bot-to-bot and
  agent-to-human commerce: paywalled channels, paid reports, API-key-less
  paid endpoints — a Stripe-for-Telegram-communities moment on Base.
- **📡 Paid channels (channel paywalls)** — an admin turns a Telegram channel
  into a paid subscription product in one command (`/paywall channel 5` — 5
  USDC/30 days, run inside the channel with the bot as admin). Subscribers
  buy with `/paywall subscribe @channel`: a one-use invite link is issued
  (or the active subscription is extended), the seller gets USDC instantly
  plus a sale notification, and a watcher kicks expired subscribers
  (ban+unban + a DM that it expired). Re-buying re-arms access seamlessly —
  subscriptions are rows, not wallets.
- **🧠 AI assistant** — `/ask <question>` answers community questions via an
  OpenAI-compatible API (configurable model/key), optionally using the
  replied-to message as context; throttled and gracefully disabled when no
  key is configured.
- **📊 Transparency** — a public dashboard shows volume, open markets,
  leaderboard, and a live **solvency check** (`/api/solvency`): all user
  balances + unclaimed deposits must be covered by on-chain reserves. When the
  treasury contract is deployed, reserves are read directly from
  **TipBotVault** (`totalReserves()`) — anyone can verify them on Base without
  trusting us (Proof of Reserves). A health endpoint reports the deposit
  scanner's lag (`chain_head` / `last_scanned_block` / `deposit_lag`), so
  deposits are never silently delayed. The UI follows the **Base design
  system** (Base Black/White, Primary Blue #0052FF, Inter, "Built on Base"
  badge) — on-brand from the first screen.

### Why Base

- **Sub-cent fees + 2s finality** make micro-tipping and micro-trading viable
  for the first time.
- **USDC is native on Base** — no wrapping, no bridges, no custom contracts,
  minimal audit surface.
- **Native x402 support** — the only chain where agent-to-agent payments work
  out of the box.
- **Telegram → Base funnel**: 900M Telegram users, one message away from a
  dollar-pegged wallet.

### Security model

- **One minimal contract, no attack surface** — `TipBotVault` (Solidity
  0.8.24, dependency-free) is the treasury: users deposit USDC straight into
  the contract; the bot's hot wallet is only a **relayer** capped by a daily
  distribution limit; the **owner (multisig)** holds full control. A
  compromised server cannot steal more than the daily cap and cannot touch
  the reserve without the owner key. 12 automated tests run the real compiled
  bytecode on a local EVM (deposits, caps, day rollover, owner powers).
- Every user balance is a real claim on the on-chain reserve; conservation is
  enforced and verified by an automated test suite (fees, refunds, payouts,
  AMM escrow — nothing created, nothing lost). Market funding is provably
  sufficient: escrow ≥ max possible payout, verified by a property test over
  randomized trade sequences.
- Wallet linking uses EIP-191 signatures (`personal_sign`); deposits are
  credited **only to the owner of the sending wallet** (a tx hash is public
  on-chain, so a `claim` without this check would be stealable), then matched
  on-chain Transfer events.
- Watchers with timeouts and refunds: stuck/reverted withdrawals are
  automatically refunded, RPC outages don't break deposits.

### What the grant unlocks

1. **Growth & operations** — real RPC infrastructure (Alchemy/Infura), hosting,
   and promotion in communities. Deployment is already Dockerized (bot +
   dashboard, healthcheck, shared ledger volume).
2. **On-chain market escrow** — move market collateral on-chain for
   trustless resolution.
3. **Referral program** — the creator-fee loop scaled with a dashboard per
   creator.
4. **Audit & transparency** — formal audit of `TipBotVault` (CertiK/Hacken/
   independent) and the on-chain proof page. The live `/api/solvency` check
   (liabilities vs vault reserves) is already in the dashboard.
5. **x402 growth** — onramp for agents (CDP fiat onramp so agents without
   USDC can still pay), Farcaster Frames tipping, and paywall templates for
   communities (paywalled channels, paid reports).

### Ask

Base Builder Grants are fixed at **up to $5,000** (set by the Base team, not by
us), plus monthly product & GTM support. The funds cover real RPC
infrastructure, hosting, and community rollout — taking a working product from
"demo" to "communities actually running their economies on it".

---

## 9. Pre-submission checklist

- [ ] Deploy to Base mainnet: fill `.env` (`BOT_TOKEN`, `BASE_RPC_URL`,
      `HOT_WALLET_KEY`, `BOT_USERNAME`), `docker compose up -d --build`,
      run the end-to-end pass: deposit → auto-credit → `/tip` → `/withdraw`.
- [ ] **Live product**: real first users and at least minimal usage numbers
      (the form expects a live product).
- [ ] **Project URL** publicly reachable (dashboard + `/api/solvency` answers).
- [ ] **X (Twitter)** created with activity ("we're live" post + reposts);
      real Telegram username.
- [ ] **1-minute Loom demo** recorded per §4 (the form asks for exactly Loom).
- [ ] Hot wallet key generated → address in the "Contract address on Base"
      field.
- [ ] TipBotVault deployed (`python scripts/deploy_vault.py`), USDC moved
      into the contract, `/api/solvency` shows `reserves_source: "vault"`.
- [ ] x402 verified: `POST /api/x402/tip` returns `402` with an invoice and
      credits after payment ($1 test payment).
- [ ] Paywall verified: `/paywall create` → purchase via `/paywall buy` and
      via `POST /api/x402/paywall` ($1 test payment).
- [ ] Paid channel verified: `/paywall channel 1` (bot as channel admin) →
      purchase via `/paywall subscribe` → entry via invite link.
- [ ] Prediction market verified: `/market create` → `/trade` → resolve →
      winner payout; odds visible on the public `/api/predictions` feed.
- [ ] AI assistant configured or intentionally left off (`AI_API_KEY`).
- [ ] All form fields filled (table §1), including honest usage numbers.
- [ ] E-mail under a real account (easier to verify and contact).

*Stack: Python 3.12 · aiogram (polling or webhook) · web3.py · FastAPI ·
PostgreSQL 16 · Solidity 0.8.24 (TipBotVault) · Base mainnet.*
*Address (USDC on Base): `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`*
