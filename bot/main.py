"""Tippy entrypoint. Run: python -m bot.main"""
import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramNetworkError
from aiogram.enums import ParseMode
from . import base, config
from . import i18n
from .handlers import router
from .ledger import async_ledger as ledger
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('tipbot')
bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

async def deposit_watcher() -> None:
    while True:
        try:
            credited = await base.poll_deposits()
        except Exception as e:
            log.warning('deposit poll failed: %s', e)
            await asyncio.sleep(config.POLL_SECONDS)
            continue
        for d in credited:
            try:
                if not (await ledger.get_settings(int(d['tg_id'])))['notify_deposits']:
                    continue
                await bot.send_message(d['tg_id'], i18n.t(i18n.norm((await ledger.get_settings(int(d['tg_id']))).get('lang')), 'deposit_notified', amount=f"{d['amount_micro'] / 10 ** config.USDC_DECIMALS:g}", tx_url=f"{config.BASESCAN_URL}/tx/{d['tx_hash']}", tx=d['tx_hash'][:18]))
            except Exception as e:
                log.warning('deposit notify failed for %s: %s', d['tg_id'], e)
        await asyncio.sleep(config.POLL_SECONDS)

async def withdraw_watcher() -> None:
    while True:
        try:
            await base.check_pending_withdraws()
        except Exception as e:
            log.warning('withdraw check failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS)

async def market_watcher() -> None:
    """Once per cycle: remind market creators to resolve markets whose deadline
    passed (both parimutuel bets and LMSR AMM markets). Without resolution the
    traders' money sits locked until the grace refund, so one nudge prevents
    'forgotten markets'. A second, final nudge goes out shortly before the
    grace period ends (after that anyone can refund)."""
    while True:
        try:
            for bet in await ledger.open_bets_past_deadline():
                await ledger.mark_deadline_notified(int(bet['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(bet['creator'])).get('lang'))
                    await bot.send_message(bet['creator'], i18n.t(creator_lang, 'deadline_notify', id=bet['id'], question=bet['question']))
                except Exception as e:
                    log.warning('deadline notify failed for #%s: %s', bet['id'], e)
            for bet in await ledger.bets_need_grace_warning(config.GRACE_WARN_BEFORE_HOURS * 3600):
                hours_left = max(1, round((bet['close_at'] + config.MARKET_GRACE_HOURS * 3600 - time.time()) / 3600))
                await ledger.mark_grace_warned(int(bet['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(bet['creator'])).get('lang'))
                    await bot.send_message(bet['creator'], i18n.t(creator_lang, 'grace_warn', id=bet['id'], question=bet['question'], hours=hours_left))
                except Exception as e:
                    log.warning('grace warn failed for #%s: %s', bet['id'], e)
            for m in await ledger.open_markets_past_deadline():
                await ledger.mark_market_deadline_notified(int(m['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(m['creator'])).get('lang'))
                    await bot.send_message(m['creator'], i18n.t(creator_lang, 'deadline_notify', id=m['id'], question=m['question']))
                except Exception as e:
                    log.warning('market deadline notify failed for #%s: %s', m['id'], e)
            for m in await ledger.markets_need_grace_warning(config.GRACE_WARN_BEFORE_HOURS * 3600):
                hours_left = max(1, round((m['close_at'] + config.MARKET_GRACE_HOURS * 3600 - time.time()) / 3600))
                await ledger.mark_market_grace_warned(int(m['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(m['creator'])).get('lang'))
                    await bot.send_message(m['creator'], i18n.t(creator_lang, 'grace_warn', id=m['id'], question=m['question'], hours=hours_left))
                except Exception as e:
                    log.warning('market grace warn failed for #%s: %s', m['id'], e)
        except Exception as e:
            log.warning('market deadline check failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS * 4)

async def channel_watcher() -> None:
    """Kick users whose paid channel access expired (once per few cycles)."""
    while True:
        try:
            await base.kick_expired_channel_subscriptions(bot)
        except Exception as e:
            log.warning('channel kick check failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS * 4)

async def housekeeping_watcher() -> None:
    """Daily DB housekeeping: prune the reaction-tip message index so tables
    do not grow forever in active groups (balances live in `users`, so no
    money-critical data is touched)."""
    while True:
        try:
            removed = await ledger.prune_message_index(config.MESSAGE_INDEX_RETENTION_SECONDS)
            if removed:
                log.info('pruned %s stale message-index rows', removed)
        except Exception as e:
            log.warning('housekeeping failed: %s', e)
        await asyncio.sleep(86400)

async def _run_webhook(stop: asyncio.Event | None=None) -> None:
    """Register the webhook with Telegram, serve the API, keep watchers alive.

    Single-process mode for hosts like Render: uvicorn serves web/server.py
    (which includes the /telegram-webhook endpoint) on $PORT while the
    deposit/withdraw/market watchers run as tasks in the same loop. Without a
    bound port the platform health check kills the service.
    """
    import os
    import uvicorn
    from web import hook
    from web.server import app as web_app
    await hook.bot.set_webhook(url=config.WEBHOOK_URL, secret_token=hook.webhook_secret())
    log.info('webhook registered: %s', config.WEBHOOK_URL)
    port = int(os.environ.get('PORT', str(config.WEB_PORT)))
    uvi = uvicorn.Server(uvicorn.Config(web_app, host=config.WEB_HOST, port=port, log_level='info'))
    server = asyncio.create_task(uvi.serve())
    wait = stop or asyncio.Event()
    stop_task = asyncio.create_task(wait.wait())
    try:
        await asyncio.wait([server, stop_task], return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_task.cancel()
        server.cancel()
        await asyncio.gather(server, stop_task, return_exceptions=True)

async def main() -> None:
    config.validate()
    log.info('hot wallet: %s', base.hot_wallet())
    from web.mini import public_base_url
    log.info('mini app url: %s', public_base_url() + '/app')
    dp = Dispatcher()
    dp.include_router(router)
    try:
        from .handlers import AI_BOT_COMMAND
        await bot.set_my_commands([AI_BOT_COMMAND, types.BotCommand(command='menu', description='Главное меню'), types.BotCommand(command='balance', description='Баланс кошелька'), types.BotCommand(command='deposit', description='Пополнить USDC'), types.BotCommand(command='withdraw', description='Вывести USDC'), types.BotCommand(command='tip', description='Чаевые USDC'), types.BotCommand(command='rain', description='Дождь: раздать USDC в чате'), types.BotCommand(command='markets', description='Рынки предсказаний'), types.BotCommand(command='market', description='Открыть рынок по id'), types.BotCommand(command='trade', description='Купить доли на рынке'), types.BotCommand(command='sell', description='Продать доли'), types.BotCommand(command='positions', description='Мои позиции и PnL'), types.BotCommand(command='bet', description='Ставка-пул: создать/поставить'), types.BotCommand(command='bets', description='Открытые ставки-пулы'), types.BotCommand(command='mybets', description='Мои ставки'), types.BotCommand(command='resolve', description='Закрыть ставку (создатель)'), types.BotCommand(command='cancel', description='Отменить свою ставку'), types.BotCommand(command='stats', description='Статистика бота'), types.BotCommand(command='top', description='Топ пользователей'), types.BotCommand(command='history', description='История операций'), types.BotCommand(command='donate', description='Твоя страница донатов'), types.BotCommand(command='link', description='Привязать внешний кошелёк'), types.BotCommand(command='confirm', description='Подтвердить привязку'), types.BotCommand(command='claim', description='Забрать с внешнего адреса'), types.BotCommand(command='wallet', description='Кошелёк: адрес и ключи'), types.BotCommand(command='import', description='Импорт по сид-фразе'), types.BotCommand(command='export', description='Выгрузить ключ и сид'), types.BotCommand(command='tx', description='Проверить транзакцию в Base'), types.BotCommand(command='paywall', description='Платные посты'), types.BotCommand(command='settings', description='Настройки'), types.BotCommand(command='language', description='Сменить язык / Language'), types.BotCommand(command='about', description='О боте — что это такое'), types.BotCommand(command='app', description='Мини-приложение')])
    except Exception as e:
        log.warning('set_my_commands failed: %s', e)
    tasks = [asyncio.create_task(deposit_watcher()), asyncio.create_task(withdraw_watcher()), asyncio.create_task(market_watcher()), asyncio.create_task(channel_watcher()), asyncio.create_task(housekeeping_watcher())]
    try:
        while True:
            try:
                if config.WEBHOOK_URL:
                    await _run_webhook()
                else:
                    await dp.start_polling(bot, skip_updates=True)
                break
            except TelegramNetworkError as e:
                # Transient network outage at startup: retry instead of dying
                # so a brief connectivity blip does not kill the whole bot.
                log.warning('telegram unreachable, retrying in 15s: %s', e)
                await asyncio.sleep(15)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
if __name__ == '__main__':
    asyncio.run(main())