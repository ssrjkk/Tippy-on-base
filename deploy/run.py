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
import signal
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


async def _start_web_server(stop: asyncio.Event | None = None) -> None:
    """Start uvicorn serving the FastAPI app."""
    import uvicorn

    from web.server import app as web_app

    port = int(os.environ.get("PORT", str(config.WEB_PORT)))
    uvi = uvicorn.Server(uvicorn.Config(
        web_app,
        host=config.WEB_HOST,
        port=port,
        log_level="info",
        # Never let uvicorn rewrite request.client from X-Forwarded-For /
        # X-Forwarded-Proto before app-level checks: uvicorn trusts those
        # headers on forwarded_allow_ips peers (e.g. a local cloudflared) and
        # would pre-inject a client-spoofed identity, defeating the app's
        # TRUSTED_PROXY_PEERS guard. real IP resolution is app-owned.
        proxy_headers=False,
    ))
    # If a stop Event is provided, wait on it so a graceful shutdown signal can
    # ask uvicorn to exit instead of the process being SIGKILLed mid-request.
    if stop is not None:
        waiter = asyncio.create_task(stop.wait())
        serve = asyncio.create_task(uvi.serve())
        try:
            await asyncio.wait([serve, waiter], return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
            if not serve.done():
                uvi.should_exit = True
                await asyncio.gather(serve, waiter, return_exceptions=True)
        return
    await uvi.serve()


# The bot-side watchers, keyed by name. Each factory is called as
# factory(bot, ledger) and returns the watcher coroutine. Kept module-level
# so a regression test can assert this set stays in sync with bot.main
# (the x402_sweep watcher previously existed only in main.py and silently
# never ran in combined mode).
WATCHERS = (
    ("deposit", lambda bot, ledger: _deposit_watcher(bot, ledger)),
    ("withdraw", lambda bot, ledger: _withdraw_watcher()),
    ("batch_withdraw", lambda bot, ledger: _batch_withdraw_watcher()),
    ("market", lambda bot, ledger: _market_watcher(bot, ledger)),
    ("channel", lambda bot, ledger: _channel_watcher(bot)),
    ("create2_sweep", lambda bot, ledger: _create2_sweep_watcher()),
    ("x402_sweep", lambda bot, ledger: _x402_sweep_watcher()),
    ("housekeeping", lambda bot, ledger: _housekeeping_watcher(ledger)),
    ("solvency", lambda bot, ledger: _solvency_watcher(bot)),
    ("onchain", lambda bot, ledger: _onchain_watcher(bot)),
    ("x402_reconcile", lambda bot, ledger: _x402_reconcile_watcher()),
    ("notification_outbox", lambda bot, ledger: _notification_outbox_worker(bot, ledger)),
)


def _watcher_done(name: str, task: asyncio.Task, stop: asyncio.Event | None) -> None:
    """Done-callback: surface a silent watcher death and request shutdown.

    A cancelled task is the normal shutdown path (not an error). A task that
    finished with an exception outside its own try/except, or returned early,
    means a background loop stopped running — log it loudly and set the stop
    event so the supervisor/health-check can restart the process instead of
    the system limping on with a dead money-path.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        log.warning("watcher '%s' returned unexpectedly — stopping process", name)
    else:
        log.error(
            "watcher '%s' died unexpectedly (exception=%r) — stopping process",
            name, exc,
        )
    if stop is not None and not stop.is_set():
        stop.set()


async def _start_bot_polling(stop: asyncio.Event | None = None) -> None:
    """Start aiogram polling + watchers.

    Watcher tasks are created with a done-callback so a silent death (a
    watcher that raises outside its own try/except, or finishes its loop
    unexpectedly) is logged instead of being invisible, and cancels the whole
    process via the stop event so the supervisor/health check can restart it.
    """
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

    tasks: list[asyncio.Task] = []
    for name, factory in WATCHERS:
        task = asyncio.create_task(factory(tg_bot, ledger))
        task.add_done_callback(
            lambda task=task, name=name: _watcher_done(name, task, stop)
        )
        tasks.append(task)

    log.info("bot polling starting")
    try:
        if stop is not None:
            waiter = asyncio.create_task(stop.wait())
            try:
                while not stop.is_set():
                    poll = asyncio.create_task(dp.start_polling(tg_bot, skip_updates=True))
                    try:
                        done, _ = await asyncio.wait([poll, waiter], return_when=asyncio.FIRST_COMPLETED)
                        if poll in done:
                            exc = poll.exception()
                            if exc is not None:
                                if isinstance(exc, TelegramNetworkError):
                                    log.warning("telegram unreachable, retrying in 15s: %s", exc)
                                    await asyncio.sleep(15)
                                    continue
                                raise exc
                            # Polling ended normally (e.g. stop set elsewhere).
                            break
                    finally:
                        if not poll.done():
                            poll.cancel()
                            await asyncio.gather(poll, return_exceptions=True)
            finally:
                waiter.cancel()
        else:
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
        try:
            await ledger.close()
        except Exception:
            log.warning("ledger close failed", exc_info=True)


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


async def _batch_withdraw_watcher():
    from bot import base
    while True:
        try:
            await base.flush_withdraw_batch()
        except Exception as e:
            log.warning("batch withdraw flush failed: %s", e)
        await asyncio.sleep(config.WITHDRAW_BATCH_FLUSH_SECONDS)


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
    """Daily DB housekeeping: prune the reaction-tip message index and the
    x402/pending side-tables so the DB stays bounded in active groups
    (balances live in `users`, so no money-critical data is touched — paid
    x402 txs are kept for a full year as the anti-replay guard)."""
    from bot import config
    while True:
        try:
            removed = await ledger.prune_message_index(config.MESSAGE_INDEX_RETENTION_SECONDS)
            if removed:
                log.info("pruned %s stale message-index rows", removed)
            counts = await ledger.prune_housekeeping(
                getattr(config, "X402_RETENTION_SECONDS", 90 * 86400),
                getattr(config, "X402_PAYMENT_RETENTION_SECONDS", 365 * 86400),
            )
            if any(counts.values()):
                log.info("pruned x402/pending rows: %s", counts)
        except Exception as e:
            log.warning("housekeeping failed: %s", e)
        await asyncio.sleep(getattr(config, "HOUSEKEEPING_INTERVAL_SECONDS", 86400))


async def _x402_sweep_watcher():
    """Consolidate USDC from per-invoice pay addresses to the x402 receive pool.

    Mirrors bot.main.x402_sweep_watcher: each x402 invoice derives a unique
    EOA that holds USDC after payment; this moves those funds to the shared
    X402_RECEIVE_ADDRESS so reconciliation sees them. Idle when x402 is
    disabled or no invoice address holds USDC.
    """
    from bot import config
    from bot.x402_sweep import sweep_all_invoices
    while True:
        try:
            if config.X402_ENABLED and config.X402_RECEIVE_ADDRESS:
                swept = await sweep_all_invoices()
                if swept:
                    log.info("x402 sweep: consolidated %d invoice(s)", swept)
        except Exception as e:
            log.warning("x402 sweep failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS)


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
            items = await ledger.dequeue_notifications()
            for n in items:
                try:
                    await bot.send_message(n["chat_id"], n["text"])
                    await ledger.ack_notification(n["id"])
                except Exception:
                    await ledger.retry_notification(n["id"], 30)
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
    """Once per cycle: remind creators to resolve markets whose deadline passed
    (both parimutuel bets and LMSR AMM markets), plus a second, final nudge
    shortly before the grace period ends (after that anyone can refund)."""
    import time as _time

    from bot import config, i18n
    while True:
        try:
            for bet in await ledger.open_bets_past_deadline():
                await ledger.mark_deadline_notified(int(bet['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(bet['creator'])).get('lang'))
                    await bot.send_message(bet['creator'], i18n.t(creator_lang, 'deadline_notify', id=bet['id'], question=bet['question']))
                except Exception as e:
                    log.warning("deadline notify failed for #%s: %s", bet['id'], e)
            for bet in await ledger.bets_need_grace_warning(config.GRACE_WARN_BEFORE_HOURS * 3600):
                hours_left = max(1, round((bet['close_at'] + config.MARKET_GRACE_HOURS * 3600 - _time.time()) / 3600))
                await ledger.mark_grace_warned(int(bet['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(bet['creator'])).get('lang'))
                    await bot.send_message(bet['creator'], i18n.t(creator_lang, 'grace_warn', id=bet['id'], question=bet['question'], hours=hours_left))
                except Exception as e:
                    log.warning("grace warn failed for #%s: %s", bet['id'], e)
            for m in await ledger.open_markets_past_deadline():
                await ledger.mark_market_deadline_notified(int(m['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(m['creator'])).get('lang'))
                    await bot.send_message(m['creator'], i18n.t(creator_lang, 'deadline_notify', id=m['id'], question=m['question']))
                except Exception as e:
                    log.warning("market deadline notify failed for #%s: %s", m['id'], e)
            for m in await ledger.markets_need_grace_warning(config.GRACE_WARN_BEFORE_HOURS * 3600):
                hours_left = max(1, round((m['close_at'] + config.MARKET_GRACE_HOURS * 3600 - _time.time()) / 3600))
                await ledger.mark_market_grace_warned(int(m['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(m['creator'])).get('lang'))
                    await bot.send_message(m['creator'], i18n.t(creator_lang, 'grace_warn', id=m['id'], question=m['question'], hours=hours_left))
                except Exception as e:
                    log.warning("market grace warn failed for #%s: %s", m['id'], e)
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

    stop = asyncio.Event()

    # Graceful shutdown: SIGTERM/SIGINT set the stop Event instead of killing
    # the process mid-write, so in-flight watchers (and the ledger) can finish
    # and close cleanly. uvicorn reads the same Event to exit its loop.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
            log.info("registered %s for graceful shutdown", sig.name)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        await asyncio.gather(
            _start_bot_polling(stop),
            _start_web_server(stop),
        )
    finally:
        if not stop.is_set():
            stop.set()
        log.info("combined runner stopped")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--web-only", action="store_true", help="Web server only (no Telegram bot)")
    ap.add_argument("--bot-only", action="store_true", help="Bot only (legacy polling, no web)")
    args = ap.parse_args()

    config.validate()

    if args.web_only:
        log.info("WEB-ONLY mode")
        asyncio.run(_run_web_only())
    elif args.bot_only:
        log.info("BOT-ONLY mode")
        asyncio.run(_run_bot_only())
    else:
        asyncio.run(_run_combined())


async def _run_web_only() -> None:
    """Web server with graceful shutdown."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await _start_web_server(stop)


async def _run_bot_only() -> None:
    """Bot polling with graceful shutdown + ledger close."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await _start_bot_polling(stop)


if __name__ == "__main__":
    main()
