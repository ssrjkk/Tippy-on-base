"""Tippy entrypoint. Run: python -m bot.main"""

import asyncio
import logging
import time

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from . import base, config
from .handlers import router
from .ledger import ledger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("tipbot")

bot = Bot(token=config.BOT_TOKEN, default=ParseMode.HTML)


async def deposit_watcher() -> None:
    while True:
        try:
            credited = base.poll_deposits()
        except Exception as e:
            log.warning("deposit poll failed: %s", e)
            await asyncio.sleep(config.POLL_SECONDS)
            continue
        for d in credited:
            try:
                if not ledger.get_settings(int(d["tg_id"]))["notify_deposits"]:
                    continue  # user turned off deposit DMs in /settings
                await bot.send_message(
                    d["tg_id"],
                    f"✅ Депозит зачислен: <b>{d['amount_micro'] / 10**config.USDC_DECIMALS:g} USDC</b>\n"
                    f"Tx: <code>{d['tx_hash'][:18]}…</code>\nБаланс: /balance",
                )
            except Exception as e:
                log.warning("deposit notify failed for %s: %s", d["tg_id"], e)
        await asyncio.sleep(config.POLL_SECONDS)


async def withdraw_watcher() -> None:
    while True:
        try:
            base.check_pending_withdraws()
        except Exception as e:
            log.warning("withdraw check failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS)


async def market_watcher() -> None:
    """Once per cycle: remind market creators to resolve markets whose deadline
    passed. Without resolution the backers' money sits locked until the grace
    refund, so one nudge prevents 'forgotten markets'. A second, final nudge
    goes out shortly before the grace period ends (after that anyone can
    refund the market and the creator loses the 2% fee)."""
    while True:
        try:
            for bet in ledger.open_bets_past_deadline():
                try:
                    await bot.send_message(
                        bet["creator"],
                        f"⏰ Рынок #{bet['id']} — «{bet['question']}» достиг дедлайна.\n"
                        f"Закрой его: /resolve {bet['id']} &lt;номер&gt;.\n"
                        f"Иначе после grace-периода любой сможет вернуть деньги.",
                    )
                except Exception as e:
                    log.warning("deadline notify failed for #%s: %s", bet["id"], e)
                ledger.mark_deadline_notified(int(bet["id"]))
            for bet in ledger.bets_need_grace_warning(config.GRACE_WARN_BEFORE_HOURS * 3600):
                hours_left = max(1, round((bet["close_at"] + config.MARKET_GRACE_HOURS * 3600 - time.time()) / 3600))
                try:
                    await bot.send_message(
                        bet["creator"],
                        f"⚠️ Рынок #{bet['id']} — «{bet['question']}» не закрыт!\n"
                        f"До автовозврата денег всем участникам осталось ~{hours_left} ч.\n"
                        f"Закрой сейчас и получи 2% комиссии: /resolve {bet['id']} &lt;номер&gt;.",
                    )
                except Exception as e:
                    log.warning("grace warn failed for #%s: %s", bet["id"], e)
                ledger.mark_grace_warned(int(bet["id"]))
        except Exception as e:
            log.warning("market deadline check failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS * 4)


async def channel_watcher() -> None:
    """Kick users whose paid channel access expired (once per few cycles)."""
    while True:
        try:
            await base.kick_expired_channel_subscriptions(bot)
        except Exception as e:
            log.warning("channel kick check failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS * 4)


async def housekeeping_watcher() -> None:
    """Daily DB housekeeping: prune the reaction-tip message index so tables
    do not grow forever in active groups (balances live in `users`, so no
    money-critical data is touched)."""
    while True:
        try:
            removed = ledger.prune_message_index(config.MESSAGE_INDEX_RETENTION_SECONDS)
            if removed:
                log.info("pruned %s stale message-index rows", removed)
        except Exception as e:
            log.warning("housekeeping failed: %s", e)
        await asyncio.sleep(86400)


async def _run_webhook(
    tasks: list[asyncio.Task], stop: asyncio.Event | None = None
) -> None:
    """Register the webhook with Telegram, then keep the watchers alive.

    The FastAPI app (web/server.py, which hosts the hook endpoint) runs as a
    separate process behind a reverse proxy; this entrypoint is only the bot
    side (webhook registration + deposit/withdraw/market watchers).
    """
    from web import hook

    await hook.bot.set_webhook(
        url=config.WEBHOOK_URL, secret_token=hook.webhook_secret()
    )
    log.info("webhook registered: %s", config.WEBHOOK_URL)
    wait = stop or asyncio.Event()
    await wait.wait()


async def main() -> None:
    config.validate()
    log.info("hot wallet: %s", base.hot_wallet())
    # The dispatcher is created here (not at import): aiogram routers may be
    # attached to a single dispatcher, so importing this module must not
    # consume `handlers.router` (it would break other dispatchers in tests).
    dp = Dispatcher()
    dp.include_router(router)
    # Keep strong references so the watcher tasks are never garbage-collected
    # (a lost asyncio.Task can be cancelled silently, stopping deposits/refunds).
    tasks = [
        asyncio.create_task(deposit_watcher()),
        asyncio.create_task(withdraw_watcher()),
        asyncio.create_task(market_watcher()),
        asyncio.create_task(channel_watcher()),
        asyncio.create_task(housekeeping_watcher()),
    ]
    try:
        if config.WEBHOOK_URL:
            await _run_webhook(tasks)
        else:
            await dp.start_polling(bot, skip_updates=True)
    finally:
        for task in tasks:
            task.cancel()
        # Await the cancelled watchers so shutdown is clean (no "Task was
        # destroyed but it is pending" warnings).
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
