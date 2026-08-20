# Tippy — Base Builder Grant Program Application

Copy-paste package for the **Base Builder Grant Program** form (owned by
Coinbase/Base). Grants: **up to $5,000** in seed capital + **monthly product &
GTM support** + **priority technical support**.

Form: `https://docs.google.com/forms/d/e/1FAIpQLSeEFi9BLm5XCm7KrFzRZC-rxcAqCNZPzWZ9He4aZkxsKuRXjw/viewform`
Announcement: `https://x.com/base/status/2086754553580355673`

Areas of interest (our fit): **Prediction markets** · Launchpads · DeFi ·
Agents · Asset creation · Consumer apps.

> ⚠️ **Критично:** программа рассчитана на **live-продукт, который уже
> используется** («You're past the idea stage. With a Live product that's
> getting used»). До деплоя на Base mainnet и первых реальных пользователей
> заявку подавать рано — сначала чек-лист §7 (деплой + демо + активность),
> потом форма. Usage numbers должны быть честными (см. §5).

---

## 1. Copy-paste answers (all form fields)

| Form field | Answer |
|---|---|
| **Full name** | `TODO: ваше имя` |
| **Email** | `TODO: e-mail под реальным аккаунтом` |
| **X (Twitter) handle** | `TODO: @Nickname` (создать + активность, см. §7) |
| **Telegram username** | `TODO: @username` |
| **Project name + one-line description** | `Tippy — turn any Telegram community into a USDC economy on Base: instant tips, QR donation pages, and parimutuel prediction markets with automatic payouts.` |
| **Tell us about the founding team** | см. §2 |
| **Link to your live product** | `TODO: https://<домен дашборда>` — публичный дашборд + `/api/solvency` |
| **Link to a Product Demo (Loom)** | `TODO: ссылка на Loom` — сценарий §3 |
| **Contract address on Base** | Бот кастодиальный: `TODO: адрес hot wallet` (после генерации ключа) — все балансы держатся на нём; единственный используемый контракт — аудированный USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`. |
| **Which track best fits?** | `Trading` (рекомендуется: прогнозные рынки — трейдинг-продукт). Альтернатива: «Другое: Prediction Markets». |
| **Key usage numbers** | Честно до деплоя: 0. Формат: all-time users / DAU / WAU / all-time volume / 30-day volume — см. §5 |
| **How does your product make money today?** | см. §6 |
| **GTM plan for the next 3 months** | см. §7 |
| **Base Builder Code** | `TODO: код пригласившего (если есть)` |
| **Primary challenge or bottleneck** | `User acquisition` — продукт готов (368 автотеста, 94% покрытие), нужны сообщества и рост |
| **Project description (без трекшна — формулировка)** | `Tippy is a monetization layer for Telegram communities and AI agents on Base. We enable creators to gate content behind USDC paywalls using the x402 protocol, with fully on-chain Proof of Reserves via TipBotVault. While currently in pre-launch phase, the technical foundation is production-ready with 368 automated tests and 94% coverage. We are targeting the 900M Telegram user base and the rapidly growing AI agent economy on Base.` |
| **Why Base?** | `Base is the only chain with native x402 support, which is critical for our AI-agent-to-agent commerce model. Additionally, Base's Smart Wallets enable gasless onboarding for Telegram users who have never used crypto before. We are building exclusively on Base to leverage these primitives.` |
| **Use of Funds ($5,000)** | `$2,000 — Smart Contract Audit (TipBotVault.sol), critical for handling user deposits. $1,500 — GTM & Partnership Development: onboarding the first 10 crypto-signal Telegram channels and integrating with Virtuals Protocol agents. $1,500 — Infrastructure scaling (PostgreSQL optimization, Alchemy Webhooks) for 10k+ daily active users.` |
| **Traction (честно, с фокусом на потенциал)** | `Currently pre-launch with a fully tested MVP. Technical milestones achieved: x402 integration, Vault Proof of Reserves, 368 tests. Next step: Launch on Base mainnet and secure the first 100 users through Farcaster Frames and AI agent partnerships.` |
| **Which credits would be most useful?** | `AWS` (хостинг бота/дашборда), `Alchemy / QuickNode` (RPC Base), `Privy` (не приоритетно — вход через Telegram) |

## 2. Founding team (draft)

> Solo builder, full-stack (Python, PostgreSQL, Telegram Bots, web3/Base).
> Prior experience: `TODO: прошлые компании/проекты, релевантный опыт`
> Funding raised: `TODO: (если было)`. Тон: продукт написан целиком и
> покрыт 368 автотестами (консервация средств, безопасность депозитов,
> E2E-сценарии) — надёжность важнее маркетинга.

## 3. The one-line-adjacent pitch (154 words — reuse for "tell us about your product" if the form asks)

> Tippy turns any Telegram community into a USDC economy on Base:
> instant tips, QR donation pages, and parimutuel prediction markets with
> automatic payouts. Everything settles instantly inside the bot's ledger
> (zero gas), while every balance is backed by a public, on-chain auditable
> treasury contract (TipBotVault) — no custom tokens, no IOU points, only
> USDC, so the audit surface is minimal.
>
> It brings users onchain: 900M+ Telegram users are one message away from a
> dollar-pegged wallet, and group admins get a built-in referral loop (2% win
> fee) that makes them want their community betting. A public dashboard shows
> volume, markets, and a live solvency check proving liabilities are always
> covered by the on-chain USDC balance.
>
> The prototype is complete (368 automated tests, 94% coverage, Dockerized,
> treasury contract deployed with 12 EVM-tested scenarios).
> The grant funds real RPC infrastructure, hosting, and community rollout.

## 4. 1-minute demo script (record on Loom after deploy)

1. `0:00–0:10` — `/start` в Telegram, меню бота.
2. `0:10–0:25` — `/deposit`, открыть дашборд (Project URL), показать QR на
   донат-странице `/u/{id}`; отправить USDC с кошелька → авто-зачисление.
3. `0:25–0:40` — `/tip 5` и реакция-чаевые в группе; показать `/top` и
   мгновенное уведомление получателя.
4. `0:40–0:55` — `/bet create ...`, ставка из двух тапов, `/resolve`,
   автоматическая выплата победителю; с любой страницы рынка — кнопка
   «🎯 Поставить в Telegram» (deep-link `t.me/bot?start=bet_<id>`).
5. `0:55–1:00` — `/api/solvency`: обязательства покрыты резервами, которые
    читаются прямо из контракта `TipBotVault` (`reserves_source: "vault"`).
6. (бонус для жюри) x402: `curl -X POST <домен>/api/x402/tip?recipient=…&amount=1`
    → `402` с инвойсом → оплата USDC → повторный запрос с `x-402-payment` → 200.
7. (киллер-фича) x402 Paywall: `/paywall create 1 Отчёт` → контент →
    `curl -X POST <домен>/api/x402/paywall?item=1&amount=1` → агент платит
    on-chain и получает контент — bot-to-bot коммерция на Base.
8. (киллер-фича 2) Платный канал: админ включает `/paywall channel 5`,
   подписчик платит `/paywall subscribe @канал`, входит по invite-линку,
   watcher кикает после истечения — канал монетизируется сам.
9. (дистрибуция) Farcaster Frame: запостить `/frame/<id>` в Warpcast с
   тегами `@jessepollak` `@buildonbase` — кнопка «Buy in Telegram» ведёт на
   `t.me/bot?start=paywall_<id>` (однокнопочная покупка), вторая кнопка —
   прямой x402-инвойс для агентов. Демо-скрипты: `scripts/x402_demo.py`,
   `scripts/agent_demo.py` (агент минтит пост и сам платит за него).

## 5. Key usage numbers — что заполнять (формат формы)

Форма запрашивает: all-time users onboarded, DAU, WAU, all-time volume,
last-30-day volume. До деплоя честно: `0 / 0 / 0 / 0 / 0` + (если есть поле
для текста) «just launched, live on Base mainnet, onboarding first
communities». После первых недель — реальные цифры из дашборда (`/api/stats`
и `/api/user`), они отображаются публично и их легко проверить: **не
завышать**.

## 6. How the product makes money

- **2% win fee на прогнозных рынках** — комиссия от чистого выигрыша
  победителя идёт **создателю рынка** (админ группы / стример). Это
  встроенная реферальная петля: админу выгодно, чтобы его сообщество
  ставило. Следующий шаг — доля платформы на escrow-рынках.
- **Конверсия в депозиты** — каждая активность (тип, донат, ставка) требует
  USDC в боте; это реальный on-chain inflow на hot wallet.
- **Донат-страницы** — комиссия на донаты (опционально, после запуска).

## 7. GTM plan (3 months)

- **Месяц 1 — деплой и первые сообщества.** Base mainnet + публичный
  дашборд; Loom-демо; посты в X/Farcaster («мы live»); онбординг 5–10
  Telegram-сообществ (крипто-чаты, спортивные фан-группы, стримеры):
  чаевые + первый рынок с реальными ставками. **Farcaster Frames** как
  первый канал трафика: paywall-пост как Frame (`/frame/<id>`), запостить
  в Warpcast с тегами `@jessepollak`/`@buildonbase`/`$DEGEN`; «AI-агент»
  демо (`scripts/agent_demo.py`) как видео для ревьюеров.
- **Месяц 2 — реферальная петля админов.** Персональные дашборды создателей
  (объём, рынки, комиссия) — админ сам зовёт сообщество; публичные
  страницы рынков с deep-link «Поставить в Telegram» для шаринга;
  интеграции и правки по обратной связи первых пользователей.
- **Месяц 3 — вирусный рост.** Рынки-«события» (матчи, эфиры) как
  вирусный контент; коллаборации с Base-сообществами; публичный
  `/api/solvency` как аргумент доверия; цель — 1k+ пользователей,
  первый 30-дневный объём.

## 8. Full-length pitch (supporting material, not for the form)

### One-liner

**USDC-powered community economy inside Telegram on Base: tips, donation pages
with QR, and prediction markets with automatic payouts — backed by an
on-chain treasury contract (TipBotVault) with publicly verifiable reserves.**

### The problem

Crypto tipping and community monetization tools have two failure modes:

1. **Custom tokens.** Creators launch a memecoin, fans buy it, then it dumps.
   The community is left holding a useless asset.
2. **Off-chain IOU apps.** Points, "credits", promises. No transparency, no
   real settlement, no trust.

Both fail because the *value is not real money the whole community already
trusts.*

### The solution

Tippy uses **USDC — the dollar on Base** — as the single primitive.
Everything is settled instantly *inside the bot's ledger* (zero gas, zero
waiting), and the hot wallet that backs every balance is **public and
auditable on-chain** (see the web dashboard).

- **💸 Tips** — `/tip 5` or reply to any message. Instant, with a leaderboard.
- **🎁 Donation pages** — every user gets a public landing page with a QR that
  links back into the bot (`t.me/bot?start=donate_<id>`).
- **🎯 Prediction markets** — parimutuel markets with deadlines. Winner takes
  the whole pot (minus a 2% fee on net profit that goes to the market creator).
  Cancel = automatic full refund to every backer. Every market has a public
  shareable page with a "Bet in Telegram" button (deep-link `?start=bet_<id>`),
  and the bot nudges the creator when a deadline passes so backers' money is
  never stuck in a forgotten market.
- **🔑 Wallet linking** — sign a message with your wallet to auto-claim any
  USDC sent to the bot's address. Withdrawals go back on-chain anytime.
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

- **Sub-cent fees + 2s finality** make micro-tipping viable for the first time.
- **USDC is native on Base** — no wrapping, no bridges, no custom contracts,
  minimal audit surface.
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
  enforced and verified by an automated test suite (fees, refunds, payouts —
  nothing created, nothing lost).
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
2. **Escrow markets** — tradeable positions, creator-referred growth.
3. **Referral program** — the win-fee loop scaled with a dashboard per creator.
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

## 9. Pre-submission checklist (RU)

- [ ] Деплой на Base mainnet: `.env` заполнен (`BOT_TOKEN`, `BASE_RPC_URL`,
      `HOT_WALLET_KEY`, `BOT_USERNAME`), `docker compose up -d --build`,
      прогон end-to-end: депозит → авто-зачисление → `/tip` → `/withdraw`.
- [ ] **Live product**: реальные первые пользователи и хотя бы минимальные
      usage numbers (форма рассчитана на live-продукт).
- [ ] **Project URL** доступен публично (дашборд + `/api/solvency` отвечает).
- [ ] Создан **X (Twitter)** с активностью (пост «мы live» + репосты);
      Telegram username реальный.
- [ ] Записано **1-минутное демо на Loom** по сценарию §4 (форма просит
      именно Loom).
- [ ] Сгенерирован ключ hot wallet → адрес в поле «Contract address on Base».
- [ ] TipBotVault развёрнут (`python scripts/deploy_vault.py`), USDC переведён
      в контракт, `/api/solvency` показывает `reserves_source: "vault"`.
- [ ] x402 проверен: `POST /api/x402/tip` отвечает `402` с инвойсом и
      зачисляет после оплаты (тест-платёж на $1).
- [ ] Paywall проверен: `/paywall create` → покупка через `/paywall buy`
      и через `POST /api/x402/paywall` (тест-платёж на $1).
- [ ] Платный канал проверен: `/paywall channel 1` (бот — админ канала) →
      покупка `/paywall subscribe` → вход по invite-линку.
- [ ] Заполнены все поля формы (таблица §1), включая честные usage numbers.
- [ ] E-mail под реальным аккаунтом (проще проверить и связаться).

*Stack: Python 3.12 · aiogram (polling или webhook) · web3.py · FastAPI · PostgreSQL 16 · Solidity 0.8.24 (TipBotVault) · Base mainnet.*
*Address (USDC on Base): `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`*