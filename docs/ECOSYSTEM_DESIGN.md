# Tippy on Base — дизайн экосистемы (инженерный)

Дополняет `STRATEGY_BASE_ECOSYSTEM.md` (позиционирование) и `BACKLOG.md` (трек задач).
Этот документ — **архитектура продукта и план построения**: как три кита (чаевые, Cally-рынки, AI-агент)
складываются в единую экономику на Base, где какие кодовые швы, и что строить первым.

Дата: 2026-09-04. Статус кодовой базы: 669/669 pytest, ruff зелёный, forge 40/40.

> **Обновление статуса P2 (2026-09-04):** Smart Wallet (ERC-4337) + Paymaster развёрнуты
> на Base Sepolia и **доказано работают**: CREATE2-аккаунты, direct handleOps и
> **gasless UserOperations со спонсорством VerifyingPaymaster** (status=1, gas=198k).
> Роуты P2 из §4.2 перешли из «планов» в «в работе/сделано» — см. §4.2 и §8.

---

## 1. Целевая архитектура (три слоя)

```
┌────────────────────────────────────────────────────────────────────┐
│ TELEGRAM BOT (дистрибуция #1)   ·  WEB/MINI APP (дистрибуция #2)   │
│  /tip  /market /oc_*  /bet      ·  dashboard + Base App mini app    │
│  /paywall  /ask  /rain          ·  Shop/Checkout (OnchainKit)       │
└──────────────┬───────────────────────────────┬─────────────────────┘
               │  внутренняя бухгалтерия        │  onchain-операции
┌──────────────▼───────────────────────────────▼─────────────────────┐
│ LEDGER (Postgres, Decimal-exact)            BASE (web3)            │
│  users/balances/escrow   ·  LMSR AMM         USDC native           │
│  markets(bets)           ·  paywall/x402     OutcomeMarket (Cally) │
│  маркеты: гибридный LMSR+паримутуэльный       = ERC-1155 shares    │
│                         ·  create2-классы     TipBotVault (резерв) │
└──────────────────────┬───────────────────────┬─────────────────────┘
                       │                       │
                 Пользователи / сообщества / AI-агенты (x402)
```

Ключевой принцип (из README): **в горячем пути нет кастомных контрактов** — чаевые/ставки/рынки
идут мгновенно через внутренний аккаунтинг; ончейн (Cally, vault, x402) — это публичный
слой доверия и межпроцессная/агентная интеграция.

---

## 2. Три кита → продуктовые контуры и кодовые швы

### 2.1 Чаевые (быстрая циркуляция USDC)
- Уже есть: `/tip`, `/rain`, эмодзи-реакции, донаты, `/withdraw`, `/link`+`/confirm`, CREATE2-классы.
- Швы: `bot/handlers/tips.py`, `bot/handlers/wallet.py`, `bot/ledger.py` (transfer/credit/debit), `bot/create2.py`.
- **Дизайн-решение для экосистемы:** чаевые как «смазка» между рынками и сигналами —
  выигрыши и сгенерированные ставки «протекают» обратно в чат через тропы. Вводим
  **каналы вовлечения** (см. §5), чтобы циркуляция была видимой и виральной.

### 2.2 Cally — Polymarket-слой (рынки на Base)
- Уже есть: ончейн `OutcomeMarket.sol` + ERC-1155, `/oc_create|buy|sell|redeem|pos`,
  офчейн гибридный LMSR (`bot/ledger.py`), `/market`, `/trade`, `/positions`.
- Швы: контракты в `contracts/`, ончейн-слой `bot/handlers/onchain*.py`, регистр `onchain_registry`.
- **Дизайн-решение:** ончейн-рынки Cally = «публичный товарный слой», офчейн-маркеты = «внутричатовый
  соальный слой». Их соединяет **общий API прогнозирования** — та же модель PnL/портфель,
  чтобы у пользователя не было когнитивного разрыва между `/trade` и `/oc_buy`.

### 2.3 AI-агент (автономная экономика + сторонние агенты)
- Уже есть: `agent/` (новости→LLM-решение→маркет→сигнал→x402-paywall), EAS-аттестации, caps/circuit breaker, MCP-сервер.
- Швы: `agent/main.py`, `agent/signals.py`, `web/server.py` (x402 endpoints).
- **Дизайн-решение:** агент — это первый «житель» экосистемы: создаёт рынки, торгует в капах,
  продаёт сигналы за x402. Следующие жители — **сторонние агенты**, которые платят нам через
  официальный x402 за данные/сигналы/доступ (см. §4.3).

---

## 3. Базовые механики экосистемы (что уже есть как фундамент)

| Механика | Где | Назначение в экосистеме |
|---|---|---|
| USDC (native, микро-юниты 1e6) | `bot/consuming.py`, конфиг | единая валюта всей экономики |
| Proof-of-reserves `/api/solvency` | `web/server.py`, TipBotVault | публичное доверие к «виртуальным» чаевым |
| Аудит-логгер `tipbot.audit` | `bot/ledger.py` | каждый credit/transfer/debit записан |
| Notification outbox | `main.py` watcher | возврат и надёжная доставка выплат |
| Гибридный LMSR+паримутуэльный | `bot/ledger.py` | ценообразование рынков, гарантированная платёжеспособность |
| x402 (invoice→pay→replay-proof) | `web/server.py` | агентные платежи «из коробки» |
| CREATE2-классы и валидация | `bot/create2.py` | self-custody депозиты без кастомного hot-path |
| Basenames резолв | `web/` / mini app | человеко-читаемая идентичность |

---

## 4. Приоритезированный план (к чему двигаться)

### 4.1 P1 — доработка ончейн-слоя (ближайшее, кодовые швы готовы)
1. **Базовая ликвидность Cally**: активировать небольшую bot-managed ликвидность на флагманских
   рынках (`/oc_create` уже умеет subsidy). Расширить концепт суточного лимита subsidy
   (`try_book_subsidy()`, в BACKLOG ✔️) на агентный.
2. **Withdraw batching** (из roadmap README): объединить `/withdraw` в пакеты → экономия газа,
   нужен новый контракт-релей или использование TipBotVault.
3. **Оракул/резолв**: сейчас `ownerResolve` + 2h dispute. Для масштаба добавить **timelock-friendly**
   multi-source oracle (Chainlink/Alchemy cross-check) с тем же правилом «dispute → final за owner».

### 4.2 P2 — дистрибуция через Base App (главный unlock) — статус: 2026-09-04
1. **Торговля из Mini App** (BACKLOG #10): **DONE (core)**. Smart Wallet (Base Accounts / ERC-4337)
   вместо приватного ключа: `SmartAccount` (CREATE2), `SmartAccountFactory`, `EntryPoint v0.6`.
   Развёрнуто на Sepolia; CREATE2-деплой и direct handleOps доказаны on-chain.
2. **Paymaster gasless** (BACKLOG #11) — чаевые/голоса без ETH у пользователя: **DONE (proof)**.
   `VerifyingPaymaster` спонсирует газ; `postOp(PostOpMode, bytes, uint256)` под EP v0.6,
   `verificationGasLimit≥150k`, deposit-фандинг на EntryPoint. Gasless handleOps показывает status=1.
   Осталось: интегрировать `approve_and_trade_sync` с реальным USDC approve+trade UserOp, выкатить
   в Mini App endpoints.
3. **Basenames** в `/tip @name.base.eth` — платёжный резолв (chat_id ↔ basename ↔ wallet).
   Backlog уже делает `display_name_for`; расширить до полноценного платёжного резолва.
4. **Кросс-дистрибуция**: Mini App sharing/deep-link на рынок/чат — виральность внутри Base App ленты.

### 4.3 AI-агент — в сторону «agentic commerce»
1. **Официальный x402** (из STRATEGY): совместимость заголовков с Payments MCP / Agentic Wallets,
   листинг в x402-каталоге → сторонние агенты платят нам из коробки.
2. **Product-market data feed как товар**: агент продаёт **market analytics / portfolio alerts** как
   x402-paywall — расширяет `sell_signal` до подписок.
3. **Spend Permissions** (BACKLOG #7): пользователь делегирует агенту лимит на торговлю его
   смарт-аккаунта — тот же примитив, что разгоняет Coinbase. Это следующий большой шаг после
   смарт-аккаунтов у аудитории.

### 4.4 Гигиена/метрики (дёшево, держит качество при росте)
- Метрики кита (GRANT): x402-transactions, agent-created markets, signal revenue, agent PnL —
  вывести на **дашборд** (есть `/api/stats`) и в CI-чарт.
- Оформить двухуровневую модель: «мои» (Cally on ETH-like shared account) vs «onchain Cally».

---

## 5. Каналы вовлечения (виральность экономики)

Цель — чтобы деньги не «зависали» внутри баланса, а циркулировали и были видны:

| Механика | Механика реализации | Эффект |
|---|---|---|
| **Выигрыш → троп**: победитель рынка может 1-tap «переслить % в чат» | кнопка после `/oc_redeem`/`/resolve` → `/tip` | выигрыш возвращается в чат — виральный луп |
| **Донат-челлендж «голова/решка»** | `/rain` поверх созданного рынка | поднимает вовлечённость и ликвидность рынка |
| **AI-сигналы в ленте** | агент публикует карточку «сигнал» в чат/ленту с paywall | агент = контент-провайдер, конвертирует внимание в USDC |
| **Лидерборд на базе экспозиции** | `/top` + лидерборд web | статус + продвижение активных пользователей |

---

## 6. Риски и ограничения (+ как смягчаем)

| Риск | Митигация |
|---|---|
| Регуляторное внимание к «prediction/betting» в США | держим формулировки «community markets / social polls»; микросуммы; не ставки на спортивные исходы в США напрямую (README/STRATEGY) |
| Self-custody и хот-кошелёк | hot-wallet = relayer-only под дневной лимит TipBotVault; owner = multisig; CREATE2 для персонального владения |
| Кастомные контракты в hot-path | принцип «без кастомных контрактов в hot-path» — ончейн только как слой доверия |
| Газ-триффинг/штыри | gas-drip с анти-дренаж кулдаунами (BACKLOG ✔️), withdraw-батчинг |
| Агент превышает лимиты | caps ($50/д, $10/тх, 20 действ/час) + circuit breaker (3 ошибки → cooldown) — уже в коде |

---

## 7. Решение по приоритету (что делаем первым)

1. **База ликвидности Cally + withdraw-батчинг** (P1) — фундамент для экономики.
2. **Торговля из Mini App через Smart Wallet** (P2 №1) — главный канал дистрибуции.
3. **Официальный x402 + data feed** (агентский трек) — вторая дистрибуция и grant-метрики.
4. Остальное P2/P3 — по мере.

Рекомендуемый порядок реализации: **4.1.1 → 4.1.2 → 4.2.1 → 4.3.1**,
дальше — итеративно по §4.2/§4.3.

---

## 8. Приложение: Smart Wallet (ERC-4337) — что развёрнуто (Sepolia)

Контракты и состояния (последний деплой, commit `7273d52`):

| Компонент | Адрес (Sepolia) | Примечание |
|---|---|---|
| EntryPoint v0.6 | `0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789` | стандартный |
| SmartAccountFactory | `0x500C2Ae2c1b3C44a462B32ACc5fBB6eaee0bf1B8` | CREATE2 |
| SmartAccount (тест) | `0x9522ACB7d1Bf3b69F0F339E8890EE798A7E1b9CD` | tg_id=987654321123 |
| VerifyingPaymaster | `0x624C979c615C6096A0bf12b8BDeEF2288e8bD555` | deposit ~0.02 ETH |

Ключевые решения, найденные на практике:

- **`postOp` сигнатура**: EP v0.6 вызывает `IPaymaster.postOp(PostOpMode, bytes, uint256)`,
  а не `postTransaction(...)`. Селекторная невязка = мгновенный revert (AA33). Исправлено.
- **`verificationGasLimit ≥ 150k`**: холодные SSTOREs в `_recordUsage` требуют ~98k газа.
  При VG=100k paymaster получает слишком мало → OOG. С VG=150k + deposit 0.015 ETH — проходит.
- **`nonce`**: читается через `EntryPoint.getNonce(sender, 0)`, а не из storage аккаунта.
- **Хеширование**: `Account.sign_message(encode_defunct(primitive=hash))`,
  `signed.raw_transaction` (web3 v7.8.0).
- **Гарантированный prefund** = `(callGas + VG*3 + preVerif) * gasPrice`.

Модуль: `bot/smart_wallet.py` (UserOp building/signing/paymaster data, create/approve+trade),
конфиг в `bot/config.py` (`SMART_WALLET_*`), тесты в `tests/test_smart_wallet.py` (14 шт).
