"""Real-time solvency monitor — checks reserves vs liabilities every cycle.

If liabilities > reserves at any point, sends an emergency Telegram alert.
This is the P0 monitoring: the canary in the coal mine for a custodial product.

Runs as an async task inside the bot process (no separate deployment).
"""
import asyncio
import logging
import time

from bot import config, base
from bot.ledger import async_ledger as ledger

log = logging.getLogger("tipbot.solvency")

# Alert state
_last_alert_ts = 0.0
_ALERT_COOLDOWN = 300  # min 5 min between alerts to avoid spam


async def solvency_watcher(bot, interval: int = 60) -> None:
    """Periodic solvency check. Sends Telegram alert if insolvent."""
    global _last_alert_ts
    while True:
        try:
            await _check_solvency(bot)
        except Exception as e:
            log.warning("solvency check failed: %s", e)
        await asyncio.sleep(interval)


async def _check_solvency(bot) -> None:
    global _last_alert_ts
    liabilities = await ledger.total_liabilities()
    pending = await ledger.pending_deposit_total()
    owed = liabilities + pending

    # Get reserves
    vault_addr = config.VAULT_ADDRESS
    if vault_addr:
        try:
            reserves = await base.vault_balance()
        except Exception:
            reserves = None
    else:
        try:
            reserves = await base.hot_balance()
        except Exception:
            reserves = None

    if reserves is None:
        return  # can't check — RPC down

    delta = reserves - owed
    solvent = reserves >= owed

    # Always log
    log.info(
        "solvency: reserves=%.2f owed=%.2f delta=%.2f solvent=%s",
        reserves / 1e6, owed / 1e6, delta / 1e6, solvent,
    )

    # Alert on insolvency or low margin (< 5% buffer)
    now = time.time()
    if now - _last_alert_ts < _ALERT_COOLDOWN:
        return

    if not solvent:
        _last_alert_ts = now
        msg = (
            f"🚨 <b>INSOLVENCY DETECTED</b>\n"
            f"Reserves: ${reserves / 1e6:.2f}\n"
            f"Liabilities: ${owed / 1e6:.2f}\n"
            f"Deficit: ${abs(delta) / 1e6:.2f}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _send_alert(bot, msg)
    elif owed > 0 and delta / owed < 0.05:
        _last_alert_ts = now
        msg = (
            f"⚠️ <b>LOW MARGIN</b> ({delta / owed * 100:.1f}%)\n"
            f"Reserves: ${reserves / 1e6:.2f}\n"
            f"Liabilities: ${owed / 1e6:.2f}\n"
            f"Buffer: ${delta / 1e6:.2f}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _send_alert(bot, msg)


async def _send_alert(bot, text: str) -> None:
    """Send alert to configured chat."""
    chat_id = config.SOLVENCY_ALERT_CHAT_ID
    if not chat_id:
        log.warning("no SOLVENCY_ALERT_CHAT_ID set, cannot send solvency alert")
        return
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        log.warning("failed to send solvency alert: %s", e)
