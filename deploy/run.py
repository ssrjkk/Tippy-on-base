"""Combined runner: Telegram bot (polling) + web server (FastAPI) in one process.

The bot needs the web server for:
  - Mini App (Telegram WebApp)
  - Public dashboard (/)
  - x402 endpoints for AI agents
  - Health checks

Usage:
    python run.py                    # bot + web server on WEB_PORT
    python run.py --web-only         # web server only (no Telegram)
    python run.py --bot-only         # bot only (legacy polling mode)
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import config

if os.environ.get("LOG_FORMAT") == "json":
    from pythonjsonlogger.json import JsonFormatter
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter("%%(asctime)s %%(levelname)s %%(name)s %%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

log = logging.getLogger("tipbot")


async def _start_web_server() -> None:
    """Start uvicorn serving the FastAPI app."""
    import uvicorn

    from web.server import app as web_app

    port = int(os.environ.get("PORT", str(config.WEB_PORT)))
    uvi = uvicorn.Server(uvicorn.Config(
        web_app,
        host=config.WEB_HOST,
        port=port,
        log_level="info",
    ))
    await uvi.serve()


async def _start_bot_polling() -> None:
    """Start aiogram polling + watchers."""
    from aiogram import Bot, Dispatcher, types
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.exceptions import TelegramNetworkError

    from bot.handlers import router
    from bot.ledger import async_ledger as ledger

    tg_bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    try:
        from bot.handlers import AI_BOT_COMMAND
        await tg_bot.set_my_commands([
            AI_BOT_COMMAND,
            types.BotCommand(command='menu', description='Главное меню'),
            types.BotCommand(command='balance', description='Баланс кошелька'),
            types.BotCommand(command='deposit', description='Пополнить USDC'),
            types.BotCommand(command='withdraw', description='Вывести USDC'),
            types.BotCommand(command='tip', description='Чаевые USDC'),
            types.BotCommand(command='rain', description='Дождь: раздать USDC в чате'),
            types.BotCommand(command='markets', description='Рынки предсказаний'),
            types.BotCommand(command='market', description='Открыть рынок по id'),
            types.BotCommand(command='trade', description='Купить доли на рынке'),
            types.BotCommand(command='sell', description='Продать доли'),
            types.BotCommand(command='positions', description='Мои позиции и PnL'),
            types.BotCommand(command='bet', description='Ставка-пул: создать/поставить'),
            types.BotCommand(command='bets', description='Открытые ставки-пулы'),
            types.BotCommand(command='mybets', description='Мои ставки'),
            types.BotCommand(command='resolve', description='Закрыть ставку (создатель)'),
            types.BotCommand(command='cancel', description='Отменить свою ставку'),
            types.BotCommand(command='stats', description='Статистика бота'),
            types.BotCommand(command='top', description='Топ пользователей'),
            types.BotCommand(command='history', description='История операций'),
            types.BotCommand(command='donate', description='Твоя страница донатов'),
            types.BotCommand(command='link', description='Привязать внешний кошелёк'),
            types.BotCommand(command='confirm', description='Подтвердить привязку'),
            types.BotCommand(command='claim', description='Забрать с внешнего адреса'),
            types.BotCommand(command='wallet', description='Кошелёк: адрес и ключи'),
            types.BotCommand(command='import', description='Импорт по сид-фразе'),
            types.BotCommand(command='export', description='Выгрузить ключ и сид'),
            types.BotCommand(command='tx', description='Проверить транзакцию в Base'),
            types.BotCommand(command='paywall', description='Платный контент'),
            types.BotCommand(command='ask', description='AI-помощник'),
        ])
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)

    tasks = [
        asyncio.create_task(_deposit_watcher(tg_bot, ledger)),
        asyncio.create_task(_withdraw_watcher()),
        asyncio.create_task(_market_watcher(tg_bot, ledger)),
        asyncio.create_task(_channel_watcher(tg_bot)),
        asyncio.create_task(_create2_sweep_watcher()),
        asyncio.create_task(_housekeeping_watcher(ledger)),
        asyncio.create_task(_solvency_watcher(tg_bot)),
        asyncio.create_task(_onchain_watcher(tg_bot)),
        asyncio.create_task(_x402_reconcile_watcher()),
        asyncio.create_task(_notification_outbox_worker(tg_bot, ledger)),
    ]

    log.info("bot polling starting")
    try:
        while True:
            try:
                await dp.start_polling(tg_bot, skip_updates=True)
                break
            except TelegramNetworkError as e:
                log.warning("telegram unreachable, retrying in 15s: %s", e)
                await asyncio.sleep(15)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _deposit_watcher(bot, ledger):
    from bot import base, i18n
    while True:
        try:
            credited = await base.poll_deposits()
        except Exception as e:
            log.warning("deposit poll failed: %s", e)
            await asyncio.sleep(config.POLL_SECONDS)
            continue
        for d in credited:
            try:
                if not (await ledger.get_settings(int(d['tg_id'])))['notify_deposits']:
                    continue
                await bot.send_message(d['tg_id'], i18n.t(
                    i18n.norm((await ledger.get_settings(int(d['tg_id']))).get('lang')),
                    'deposit_notified',
                    amount=f"{d['amount_micro'] / 10 ** config.USDC_DECIMALS:g}",
                    tx_url=f"{config.BASESCAN_URL}/tx/{d['tx_hash']}",
                    tx=d['tx_hash'][:18],
                ))
            except Exception as e:
                log.warning("deposit notify failed for %s: %s", d['tg_id'], e)
        await asyncio.sleep(config.POLL_SECONDS)


async def _withdraw_watcher():
    from bot import base
    while True:
        try:
            await base.check_pending_withdraws()
        except Exception as e:
            log.warning("withdraw check failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS)


async def _create2_sweep_watcher():
    """Move USDC from per-user CREATE2 proxies to the hot wallet.

    The proxy only holds funds; until ``forward()`` runs, the deposit scanner
    (which watches the hot wallet) never sees them. Idle when CREATE2 is
    disabled or no proxy holds USDC.
    """
    from bot import config, create2
    while True:
        try:
            if create2.is_create2_enabled():
                swept = await create2.sweep_all_proxies()
                if swept:
                    log.info("create2 sweep: forwarded for %s", swept)
        except Exception as e:
            log.warning("create2 sweep failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS)


async def _housekeeping_watcher(ledger):
    """Daily DB housekeeping: prune the reaction-tip message index so tables
    do not grow forever in active groups (balances live in `users`, so no
    money-critical data is touched)."""
    from bot import config
    while True:
        try:
            removed = await ledger.prune_message_index(config.MESSAGE_INDEX_RETENTION_SECONDS)
            if removed:
                log.info("pruned %s stale message-index rows", removed)
        except Exception as e:
            log.warning("housekeeping failed: %s", e)
        await asyncio.sleep(86400)


async def _x402_reconcile_watcher():
    from bot import config
    from web.x402 import reconcile_stale_x402
    while True:
        try:
            n = await reconcile_stale_x402()
            if n:
                log.warning("x402 reconcile finalized %d stale payment(s)", n)
        except Exception as e:
            log.warning("x402 reconcile failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS * 8)


async def _notification_outbox_worker(bot, ledger):
    while True:
        try:
            items = await asyncio.to_thread(ledger.dequeue_notifications)
            for n in items:
                try:
                    await bot.send_message(n["chat_id"], n["text"])
                    await asyncio.to_thread(ledger.ack_notification, n["id"])
                except Exception:
                    await asyncio.to_thread(ledger.retry_notification, n["id"], 30)
        except Exception as e:
            log.warning("notification outbox worker failed: %s", e)
        await asyncio.sleep(5)


async def _solvency_watcher(bot):
    """P0 solvency/vault monitor: alert if liabilities exceed on-chain USDC."""
    from bot.solvency import solvency_watcher

    await solvency_watcher(bot)


async def _onchain_watcher(bot):
    """DM creators of closed on-chain markets; auto-cancel overdue ones."""
    from bot.handlers.onchain import onchain_watcher

    await onchain_watcher(bot)


async def _market_watcher(bot, ledger):
    from bot import i18n
    while True:
        try:
            for bet in await ledger.open_bets_past_deadline():
                await ledger.mark_deadline_notified(int(bet['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(bet['creator'])).get('lang'))
                    await bot.send_message(bet['creator'], i18n.t(creator_lang, 'deadline_notify', id=bet['id'], question=bet['question']))
                except Exception as e:
                    log.warning("deadline notify failed for #%s: %s", bet['id'], e)
            for m in await ledger.open_markets_past_deadline():
                await ledger.mark_market_deadline_notified(int(m['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(m['creator'])).get('lang'))
                    await bot.send_message(m['creator'], i18n.t(creator_lang, 'deadline_notify', id=m['id'], question=m['question']))
                except Exception as e:
                    log.warning("market deadline notify failed for #%s: %s", m['id'], e)
        except Exception as e:
            log.warning("market deadline check failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS * 4)


async def _channel_watcher(bot):
    from bot import base
    while True:
        try:
            await base.kick_expired_channel_subscriptions(bot)
        except Exception as e:
            log.warning("channel kick check failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS * 4)


async def _run_combined() -> None:
    """Run bot polling + web server concurrently in one process."""
    log.info("=== COMBINED MODE: bot + web server ===")
    try:
        from web3 import Web3
        addr = Web3().eth.account.from_key(config.HOT_WALLET_KEY).address if config.HOT_WALLET_KEY else None
    except Exception:
        addr = None
    log.info("hot wallet configured: %s", addr if addr else "MISSING")

    from web.mini import public_base_url
    log.info("mini app url: %s/app", public_base_url())

    config.validate()

    await asyncio.gather(
        _start_bot_polling(),
        _start_web_server(),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--web-only", action="store_true", help="Web server only (no Telegram bot)")
    ap.add_argument("--bot-only", action="store_true", help="Bot only (legacy polling, no web)")
    args = ap.parse_args()

    config.validate()

    if args.web_only:
        log.info("WEB-ONLY mode")
        asyncio.run(_start_web_server())
    elif args.bot_only:
        log.info("BOT-ONLY mode")
        asyncio.run(_start_bot_polling())
    else:
        asyncio.run(_run_combined())


if __name__ == "__main__":
    main()
