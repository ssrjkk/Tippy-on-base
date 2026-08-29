# BACKLOG — незакрытые моменты и улучшения

Обновлён: 2026-08-29. Состояние кодовой базы: 630/630 pytest, forge 40/40,
ruff/i18n/validate_env — зелёные. Всё ниже — то, что ОСТАЛОСЬ.

## 🔴 P0 — перед включением ончейн-слоя в проде

| # | Задача | Зачем | Оценка |
|---|---|---|---|
| 1 | Деплой OutcomeMarket с **multisig-владельцем** (`--owner <Safe>`) | owner может ownerResolve любой рынок — с hot-ключом это единственный red flag | S |
| 2 | Smoke на **Base Sepolia**: деплой + полный цикл /oc_create→buy→resolve→redeem | живой тест против настоящего USDC (FiatTokenV2 домен не был исполнен на реальной сети) | M |
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
| 18 | CSP `unsafe-inline` → nonce-based CSP для всех шаблонов | L |
| ~~19~~ | ~~README roadmap: отметить Cally как shipped~~ | ✅ |

## ⚫ Принятые трейдоффы (задокументированы, менять не планируется)

- Stray USDC → OutcomeMarket: stranded dust (NatSpec), только rescueETH.
- Негативный кэш Basenames 5 минут: свежепривязанный кошелёк ждёт до 5 мин.
- CSP: `frame-ancestors` Telegram + unsafe-inline скрипты.
- Диспут: 1 на рынок, финал за владельцем (trust model).
- `deploys`: OWNER_KEY в env для деплоя — только для тестнета; mainnet = multisig.
