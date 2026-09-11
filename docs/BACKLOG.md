# BACKLOG — незакрытые моменты и улучшения

Обновлён: 2026-09-11. Состояние кодовой базы: 750/750 pytest, ruff/i18n/validate_env — зелёные. Всё ниже — то, что ОСТАЛОСЬ.

## 🔴 P0 — перед включением ончейн-слоя в проде

> ✅ **P0 полностью закрыт** (2026-08-30): Smoke #2 на Base Sepolia, деплой #1 на Base mainnet с Safe owner.

| # | Задача | Зачем | Оценка |
|---|---|---|---|
| ~~1~~ | ~~Деплой OutcomeMarket с **multisig-владельцем** (`--owner <Safe>`)~~ | ✅ Base mainnet `0xBA6F07331ABDb33d319095E0c78f79Fc0b68681E`. Owner=`0x0635...e2` (Safe 1/1). Gas=4,250,810. `OUTCOME_MARKET_ADDRESS` в .env | ~~S~~ |
| ~~2~~ | ~~Smoke на **Base Sepolia**: деплой + полный цикл /oc_create→buy→resolve→redeem~~ | ✅ `scripts/smoke_full_cycle.py` — 8 инвариантов зелёные на Base Sepolia. TestUSDC + OutcomeMarket deploys, createMarket(2,$10,90s) → buy(500K shares @ ~$0.50) → ownerResolve(winner=1) → redeem. Profit: $247K micro-USDC. L2 RPC requires polling for state propagation. | ~~M~~ |
| ~~3~~ | ~~Пуш `dcef58c` + зелёный прогон CI на GitHub~~ | ✅ запушено (`f6f51e7`, `088fa6b`) | ~~S~~ |

## 🟠 P1 — деньги: закрываемые кодом (можно делать сейчас)

| # | Задача | Детали | Оценка |
|---|---|---|---|
| ~~4~~ | ~~**Атомарный gas-бюджет**~~ | ✅ `try_book_gas_drip()` — атомарный INSERT…RETURNING | ~~S~~ |
| ~~5~~ | ~~**x402 авто-ретрай 502-кейса**~~ | ✅ `reconcile_stale_x402()` + watcher в `main.py` | ~~S~~ |
| ~~6~~ | ~~**Батчинг redeem/cancelExpired** в OutcomeMarket~~ | ✅ `redeemMany(uint256[])` + `claimCancelledMany(uint256[])` в контракте, ABI + Python wrapper | ~~M~~ |
| 7 | **Spend Permissions**: делегирование бюджета агенту через Smart Wallet (coinbase/spend-permissions) | агент торгует в лимитах пользователя — ключевой agentic-commerce примитив Coinbase | L |
| ~~8~~ | ~~**Лимит subsidy на сутки** для /oc_create~~ | ✅ `try_book_subsidy()` + `market_subsidies` table + wired into `/oc_create` | ~~S~~ |

## 🟡 P2 — дистрибуция и UX (стратегия Coinbase)

| # | Задача | Детали | Оценка |
|---|---|---|---|
| ~~9~~ | ~~**MiniKit SDK** в /app~~ | ✅ `@farcaster/miniapp-sdk` CDN + `sdk.actions.ready()` + `addMiniApp` button | ~~M~~ |
| 10 | **Торговля из Mini App** (сейчас display-only + подсказка): подписание через Smart Wallet (Base Accounts) вместо приватного ключа | убирает /withdraw-фандинг из флоу | L |
| 11 | **Paymaster (gasless)** через CDP — требует CDP-аккаунт | чаевые и голосования без ETH у пользователя | M |
| ~~12~~ | ~~**Basenames в донатах и профилях**~~ | ✅ `display_name_for` fallback в donate landing, market creator, user profile, mini app leaderboard | ~~S~~ |
| 13 | **Уведомления Base App** через mini app webhook (сейчас только лог) | победителям рынков — нотификация в ленте | M |

## 🟢 P3 — гигиена кода (космос, но дешёво)

| # | Задача | Детали |
|---|---|---|
| ~~14~~ | ~~`bot/chain/deposits.py` — дубль логики `base.py`~~ | ✅ удалён, импорты почищены |
| ~~15~~ | ~~`estimate_buy_shares` — мёртвый код~~ | ✅ удалён |
| ~~16~~ | ~~`bot/cache.py` (Redis) и relayer pool~~ | ✅ удалён вместе с `test_cache.py` |
| ~~17~~ | ~~`eip1559_fees_sync`: `priority_wei` → `priority_gwei`~~ | ✅ переименован |
| ~~18~~ | CSP `unsafe-inline` → nonce-based CSP для всех шаблонов | ✅ `_nonce_inject()` в middleware: per-request nonce на inline `<script>`, `esm.sh`/`jsdelivr` в whitelist. Все inline-обработчики (`onclick=` и т.п.) заменены на data-act делегирование. `style-src 'self' 'unsafe-inline'` оставлен намеренно — иначе браузер блокирует style-атрибуты |
| ~~19~~ | ~~README roadmap: отметить Cally как shipped~~ | ✅ |

### ✅ Round 5 (2026-09-11) — деньги, безопасность, доки

| # | Задача | Статус |
|---|---|---|
| R5.1 | Paywall overpay: EIP-3009 overpay капается ценой поста (`credit_amount = min(settled, price_micro)`) | ✅ `web/x402.py` + регресс-тест |
| R5.2 | `credit()` отклоняет отрицательные суммы (защита от «минта» баланса) | ✅ + регресс-тест |
| R5.3 | Own-market guard в БД (`buy_shares` → `ownmarket`) | ✅ + регресс-тест |
| R5.4 | Атомарный суточный кап `/withdraw` в одной транзакции с дебетом | ✅ + существующий атомарный тест |
| R5.5 | Персистентность caps relayer-пула (`RELAYER_STATE_FILE`) + валидация ключей в `validate_env.py` | ✅ |
| R5.6 | HTML-escaping всех пользовательских строк в ответах бота (paywall/onchain/markets/bets/errors) | ✅ |
| R5.7 | Приватные чаты для `/withdraw /deposit /claim /link /confirm` + `/paywall subscribe` | ✅ |
| R5.8 | `login_nonces`: TTL-прунинг (`LOGIN_NONCE_TTL_SECONDS`) | ✅ |
| R5.9 | Агент fail-closed: ошибка LLM = нет действия; валидация капов на старте; предупреждение при локал-фоллбеке EAS | ✅ |
| R5.10 | Доки актуализированы (README, ARCHITECTURE, SECURITY, ECOSYSTEM_DESIGN, DEPLOY, .env.example, prod.env.example) | ✅ |

### ✅ Round 3–4 (2026-08-29) — тесты, перф и надёжность

| # | Задача | Статус |
|---|---|---|
| B3 | Тесты `/api/mini/*` (9 шт: state/auth/tip/trade/create/lang) | ✅ `tests/test_mini_api.py` |
| B4 | Тесты `_buy_core`/`_sell_core` (7 шт, моки цепочки) | ✅ `tests/test_onchain_handlers.py` |
| C1 | N+1 в `/api/markets` → batch `bulk_market_views()` | ✅ |
| C2 | Параллельные RPC `totalSupply` через `asyncio.gather` | ✅ |
| C4 | `log.debug/warning` в 7 критичных silent-`except` | ✅ server/base/tips |
| C5 | **Audit-логгер** `tipbot.audit` для credit/transfer/debit | ✅ |
| C6 | **Notification outbox** (retry с backoff до 3600s) + worker | ✅ 4 теста |
| C7 | **Exponential backoff** в deposit watcher (до 120s) | ✅ |

## ⚫ Принятые трейдоффы (задокументированы, менять не планируется)

- Stray USDC → OutcomeMarket: stranded dust (NatSpec), только rescueETH.
- Негативный кэш Basenames 5 минут: свежепривязанный кошелёк ждёт до 5 мин.
- CSP: `frame-ancestors` Telegram; per-request nonce на inline-скрипты + whitelist esm.sh/jsdelivr; `style-src 'unsafe-inline'` только для style-атрибутов (script-src строгий).
- Диспут: 1 на рынок, финал за владельцем (trust model).
- `deploys`: OWNER_KEY в env для деплоя — только для тестнета; mainnet = multisig.
