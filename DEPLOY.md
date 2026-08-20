# Деплой Base TipBot на Base mainnet — пошаговая инструкция

Полный путь: от подготовки аккаунтов до работающего бота + дашборда и
финального E2E-прогона. Время: ~1 час (без ожидания транзакций).

---

## Этап 0. Подготовка (локально, без сервера)

### 0.1. Бот в Telegram (@BotFather)

1. Открой `@BotFather` → `/newbot` → имя и username (username нужен без `@`).
2. Запиши **токен** (вида `123456:ABC...`).
3. `/mybots` → твой бот → **Edit Bot** → задай:
   - описание (одна строка про USDC-чаевые и рынки),
   - команды (опционально, позже можно через `/setcommands`).
4. Напиши своему боту `/start` — он появится в списке контактов.

> `BOT_USERNAME` без `@` — обязателен для кнопок `t.me/...` на дашборде
> (донат-страницы, «Поставить в Telegram»).

### 0.2. RPC для Base (mainnet)

Публичный `https://mainnet.base.org` rate-limited и нестабилен для
`eth_getLogs` — **для прода нужен свой ключ**:

- **Alchemy**: alchemy.com → бесплатный тариф → сеть Base → URL вида
  `https://base-mainnet.g.alchemy.com/v2/<KEY>`
- или **Infura** / **QuickNode** (бесплатные тарифы тоже подходят).

Запиши URL — он пойдёт в `.env` как `BASE_RPC_URL`.

### 0.3. Hot wallet (кошелёк бота)

Кошелёк уже сгенерирован в `.env` (раздел `HOT_WALLET_KEY`), публичный адрес:

```
0x862b4C5ab70a9b6B432cBF0dFD8a28230ccf0b67
```

Пополни его с любого кошелька/биржи (Base network!):

| Что | Зачем | Минимум |
|---|---|---|
| **ETH** | газ на выводы пользователей | ~$5 (0.002 ETH) |
| **USDC** | стартовая ликвидность выплат | $20–50 |

Проверь пополнение на `basescan.org/address/0x862b...`.

> ⚠️ Нельзя пополнить «потом»: первый же вывод пользователя жжёт газ из
> этого кошелька. Если ETH кончится — выводы будут падать и автоматически
> рефандиться (это корректно, но неприятно).

### 0.4. Заполни `.env` (уже создан, отредактируй)

```
BOT_TOKEN=        # ← токен из 0.1
BASE_RPC_URL=     # ← URL из 0.2 (заменить mainnet.base.org)
HOT_WALLET_KEY=   # уже заполнен — НЕ трогать и НЕ коммитить
BOT_USERNAME=     # ← username бота без @
```

Остальные переменные уже с дефолтами (комиссии 1%/2%, лимиты, защита от
спама). `.env` в `.gitignore` — убедись, что он не попадёт в git.

Проверка локально (опционально, перед сервером):

```bash
pip install -r requirements.txt
python -c "from bot import base; print(base.hot_wallet())"   # должен вывести 0x862b...
python -c "from bot import config; print(config.BOT_USERNAME, config.BASE_RPC_URL)"
```

---

## Этап 1. Сервер

### 1.1. Минимальные требования

- VPS/VDS с **Ubuntu 22.04/24.04**, 1–2 GB RAM, 10 GB SSD (хватит с запасом).
- Публичный IP. Бот работает через long-polling — **входящий порт не нужен
  для Telegram**, только для дашборда.
- Желательно: домен для дашборда (можно бесплатный subdomain у хостера).

### 1.2. Установка Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker          # или выйти и зайти заново
docker --version       # проверка
```

### 1.3. Перенос проекта

Вариант А — по git (рекомендуется, так проще обновляться):

```bash
# на сервере
mkdir -p /opt/tipbot && cd /opt/tipbot
# скопируй проект (репозиторий) сюда; .env НЕ в git — загрузи отдельно:
```

Вариант Б — с локальной машины (scp/rsync):

```bash
# с локальной машины (Windows PowerShell):
scp -r D:\base\tipbot user@SERVER_IP:/opt/tipbot
# затем на сервере удали лишнее:
cd /opt/tipbot && rm -rf .git data .pytest_cache .benchmarks
```

`.env` передавай отдельно, чтобы не потерять в git:

```bash
scp D:\base\tipbot\.env user@SERVER_IP:/opt/tipbot/.env
# на сервере:
chmod 600 /opt/tipbot/.env
```

> ⚠️ После переноса убедись, что `.env` есть на сервере:
> `ls -la /opt/tipbot/.env` — иначе контейнеры не стартуют.

---

## Этап 2. Запуск

```bash
cd /opt/tipbot
docker compose up -d --build
```

Проверка по шагам:

```bash
# 1. все сервисы запущены (db + bot + web + backup)
docker compose ps          # db: Up (healthy), web: Up (healthy)

# 2. логи бота — должен появиться "hot wallet: 0x862b..." и polling
docker compose logs -f bot

# 3. здоровье дашборда (на сервере)
curl -s http://localhost:8000/api/health

# 4. обязательства vs on-chain баланс (на сервере; в первый момент 0/0)
curl -s http://localhost:8000/api/solvency
```

Ожидаемый ответ `/api/health`:

```json
{"status": "ok", "chain_head": 21000000, "last_scanned_block": 21000000, "deposit_lag": 0, ...}
```

---

## Этап 2.5. On-chain казначейство (TipBotVault) — Proof of Reserves

Контракт `contracts/TipBotVault.sol` делает резервы публично проверяемыми:
USDC пользователей лежит в контракте, а не на EOA. Бот-релайер (горячий
кошелёк) получает дневной лимит выплат, владелец (мультисиг) — полный
контроль.

1. Подготовь кошелёк **владельца** (мультисиг-адрес Safe/Gnosis или
   отдельный холодный ключ) с ETH на газ. Скрипт требует dev-зависимости
   (solc 0.8.24) — установи их локально один раз:

   ```bash
   pip install -r requirements-dev.txt
   export OWNER_KEY=0x...   # НЕ клади в .env, только в команду/keystore
   python scripts/deploy_vault.py            # деплой + setDailyLimit + .env
   python scripts/deploy_vault.py --daily-usdc 5000   # лимит релайера, USDC/сутки
   ```

   Скрипт скомпилирует контракт (solc 0.8.24), задеплоит с owner = твой
   адрес и relayer = горячий кошелёк бота, запишет `VAULT_ADDRESS` в `.env`.

2. Переведи стартовый пул USDC с горячего кошелька **на адрес vault**
   (все будущие депозиты пользователи шлют на vault — адрес показывается
   в `/deposit` и на дашборде).

3. Проверь: `/api/solvency` теперь отдаёт `reserves_source: "vault"` и
   `vault_balance_usdc` — баланс читается напрямую из контракта.

4. (Опционально) передай владение мультисигу:

   ```bash
   python -c "from web3 import Web3; ..."   # или через Safe UI: vault.setDailyLimit / transferOwnership
   ```

> Взлом релайера ≠ потеря денег: без ключа владельца он не может
> распределить больше дневного лимита и не может вывести резерв.

---

## Этап 3. Публичный доступ к дашборду

Бот работает без веб-порта, но дашборд нужен публично (страницы рынков,
QR-донаты, solvency — всё это для гранта и для пользователей).

### 3.1. Минимум: открыть порт 8000

```bash
sudo ufw allow 8000/tcp
# дашборд: http://SERVER_IP:8000
```

### 3.2. Правильно: домен + HTTPS (рекомендуется)

Поставь **Caddy** (сам выпускает Let's Encrypt сертификаты):

```bash
sudo apt install -y caddy
sudo nano /etc/caddy/Caddyfile
```

```
tipbot.example.com {
    reverse_proxy localhost:8000
}
```

```bash
sudo systemctl reload caddy
# дашборд: https://tipbot.example.com
```

> URL вида `https://tipbot.example.com` идёт в форму гранта («Link to your
> live product») и в QR-коды донат-страниц.

### 3.3. Webhook-режим (вместо long polling)

Polling работает и без домена, но webhook надёжнее (Telegram сам доставляет
апдейты; меньше RPC-нагрузки). Нужен HTTPS-домен (уже есть, шаг 3.2):

```bash
# .env
WEBHOOK_URL=https://tipbot.example.com
WEBHOOK_PATH=/telegram-webhook
# WEBHOOK_SECRET=          # пусто -> авто-вывод из BOT_TOKEN
```

Caddyfile (тот же домен, что в 3.2):

```
tipbot.example.com {
    reverse_proxy localhost:8000
}
```

Перезапуск: `docker compose restart bot web` — при старте `bot` сам вызывает
`setWebhook` (в логах: `webhook registered: https://tipbot.example.com`).
Проверка: `curl -s https://api.telegram.org/bot<TOKEN>/getWebhookInfo` →
`url` = твой домен, `last_error_date` пуст.

> Один процесс, один порт: webhook-эндпоинт `POST /telegram-webhook` живёт
> внутри FastAPI (`web/hook.py`), секрет проверяется по заголовку
> `X-Telegram-Bot-Api-Secret-Token` (403 при несовпадении), кривые апдейты
> не заставляют Telegram пересылать их вечно (отвечаем 200).

---

## Этап 4. Финальный E2E-прогон на mainnet

Проверяется **реальная работа** (не «ответил ли сервер», а прошли ли деньги):

| Шаг | Действие | Ожидаемый результат |
|---|---|---|
| 1 | Отправь своему боту `/start` | меню бота, ты в базе |
| 2 | `/deposit` | QR-фото с адресом бота + кнопка на basescan |
| 3 | Отправь **1 USDC** на `0x862b...` со своего кошелька (Base) | в течение ~15–30 c приходит **DM: «✅ Депозит зачислен: 1 USDC»** |
| 4 | `/balance` | «1 USDC» |
| 5 | `/tip 0.5` кому-нибудь (или `/link` + `/withdraw`) | мгновенный перевод внутри бота |
| 6 | Создай группу и `/rain 1 2` в ней | участники получили USDC, тебе написали кого |
| 7 | `/settings` → выключи «Уведомления о депозитах» | следующие депозиты без DM |
| 8 | `/withdraw <твой адрес> 1` | tx появляется на basescan.org; через минуту USDC на твоём кошельке |
| 9 | Открой `https://<домен>/api/solvency` | обязательства ≤ резервов (vault или hot wallet; сходятся с шагом 8) |
| 10 | Проверь логи: `docker compose logs bot` | «deposit» и «withdraw» без ошибок |

Если на шаге 3 DM не пришёл за минуту — см. «Траблшутинг» ниже.

Тестовые средства верни себе (шаг 6) — на сервере не держи лишнего.

---

## Этап 5. Демо для гранта (Loom)

1. Запиши экран: `/start` → `/deposit` → депозит → DM → `/tip` → рынок →
   `/api/solvency` (сценарий в `GRANT.md`, §4).
2. Залей в **Loom** (форма гранта просит именно Loom), сохрани ссылку.
3. Заполни форму по таблице `GRANT.md` §1.

---

## Обслуживание

### Логи

```bash
docker compose logs -f bot      # бот
docker compose logs -f web      # дашборд
```

### Обновление

```bash
cd /opt/tipbot
git pull                        # или скопировать новую версию
docker compose up -d --build
```

### Бэкап БД (PostgreSQL)

Автоматический бэкап уже в compose: сервис `backup` раз в 6 часов снимает
`pg_dump | gzip` в volume `backups_data` и хранит копии 14 дней:

```bash
docker compose up -d --build      # поднимет и backup
docker compose logs -f backup     # следить за бэкапами
```

Ручной бэкап:

```bash
docker compose exec db pg_dump -U tipbot -d tipbot | gzip > tipbot-$(date +%F).sql.gz
```

Восстановление:

```bash
docker compose exec -T db psql -U tipbot -d tipbot < tipbot-2026-08-19.sql
docker compose restart bot web
```

Копии лежат в volume `backups_data` (`docker compose exec backup ls /backups`).
Бэкап — это вся пользовательская история и балансы, не пропускай.

### Безопасность (коротко)

- `.env` — `chmod 600`, никогда в git/чаты/скриншоты.
- SSH — только по ключу, `ufw` закрыт кроме 22/443/80.
- Обновления ОС: `sudo apt update && sudo apt upgrade`.

---

## Траблшутинг

| Симптом | Причина | Решение |
|---|---|---|
| Контейнер bot не стартует | пустой `BOT_TOKEN` / нет `.env` | заполни `.env`, `docker compose up -d` |
| `KeyError: 'BOT_TOKEN'` в логах | `.env` не подхватился | проверь `docker compose config` |
| Депозит не зачисляется | RPC публичный/rate-limited | поставь Alchemy/Infura в `BASE_RPC_URL` |
| `deposit_lag` растёт | сканер отстаёт от head | см. `docker compose logs bot`; проверь RPC |
| Вывод «Ошибка отправки» + рефанд | нет ETH на hot wallet | пополни кошелёк, повтори |
| Дашборд не открывается снаружи | порт закрыт | `sudo ufw allow 8000/tcp` или Caddy (3.2) |
| healthcheck Failed | `/api/health` недоступен внутри | `docker compose logs web` |
| Часы сервера ушли | подписи nonce «устарели» | `timedatectl set-ntp true` |

---

## Чек-лист перед формой гранта (из GRANT.md §9)

- [ ] live на mainnet: `/start` → депозит → DM → `/tip` → `/withdraw` прошли
- [ ] `https://<домен>` отвечает, `/api/solvency` публичный
- [ ] TipBotVault развёрнут (`python scripts/deploy_vault.py`), `reserves_source: "vault"`
- [ ] X-аккаунт с постом «мы live»
- [ ] Loom-демо по сценарию GRANT.md §4
- [ ] `.env` на сервере, бэкап БД настроен