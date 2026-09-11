"""Real-time solvency monitor — checks reserves vs liabilities every cycle.

If liabilities > reserves at any point, sends an emergency Telegram alert.
This is the P0 monitoring: the canary in the coal mine for a custodial product.

Runs as an async task inside the bot process (no separate deployment).
"""
import asyncio
import logging
import time

from bot import base, config
from bot.ledger import async_ledger as ledger

log = logging.getLogger("tipbot.solvency")

_MICRO = 10 ** config.USDC_DECIMALS

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
    try:
        liabilities = await ledger.total_liabilities()
        pending = await ledger.pending_deposit_total()
        owed = liabilities + pending
    except Exception as e:
        log.warning("solvency: failed to read liabilities: %s", e)
        return

    # Get reserves
    vault_addr = config.VAULT_ADDRESS
    try:
        if vault_addr:
            reserves = await base.vault_balance()
        else:
            reserves = await base.hot_balance()
    except Exception:
        reserves = None

    if reserves is None:
        # RPC down: we literally cannot see the backing, so there is no
        # insolvency verdict. That blindness is itself an emergency — alert the
        # operator (rate-limited) instead of quietly pretending it is fine.
        now = time.time()
        if now - _last_alert_ts >= _ALERT_COOLDOWN:
            _last_alert_ts = now
            await _send_alert(
                bot,
                "🚨 <b>SOLVENCY CHECK BLIND</b>\n"
                "RPC unreachable — cannot verify reserves.\n"
                f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            )
        return

    # x402 incoming: billed tips are a liability the moment they land in user
    # balances, but the USDC backing them sits in the per-payment derived
    # address (until the sweep consolidates it into the receive pool, and the
    # next sweep into the hot wallet). Neither address is the hot wallet/vault,
    # so without this addend the canary under-states reserves by the whole
    # in-flight x402 float whenever a sweep is delayed or the gas-drip daily
    # budget is exhausted. The two lines don't overlap on-chain: an invoice is
    # 'unswept' only while its funds are still in the derived address, and is
    # marked swept (so its parked amount drops out of the DB line) right after
    # the transfer has confirmed into the pool.
    try:
        reserves_micro = round(reserves * _MICRO) + await ledger.x402_unswept_credit_total()
    except Exception as e:
        log.warning("solvency: x402 unswept read failed: %s", e)
        reserves_micro = round(reserves * _MICRO)
    try:
        reserves_micro += round(await base.receive_pool_balance() * _MICRO)
    except Exception as e:
        log.warning("solvency: x402 pool read failed: %s", e)

    owed = liabilities + pending
    delta = reserves_micro - owed
    solvent = reserves_micro >= owed

    # Always log
    log.info(
        "solvency: reserves=%.2f owed=%.2f delta=%.2f solvent=%s",
        reserves_micro / _MICRO, owed / _MICRO, delta / _MICRO, solvent,
    )

    # Alert on insolvency or low margin (< 5% buffer)
    now = time.time()
    if now - _last_alert_ts < _ALERT_COOLDOWN:
        return

    if not solvent:
        _last_alert_ts = now
        msg = (
            f"🚨 <b>INSOLVENCY DETECTED</b>\n"
            f"Reserves: ${reserves_micro / _MICRO:.2f}\n"
            f"Liabilities: ${owed / _MICRO:.2f}\n"
            f"Deficit: ${abs(delta) / _MICRO:.2f}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _send_alert(bot, msg)
    elif owed > 0 and delta / owed < 0.05:
        _last_alert_ts = now
        msg = (
            f"⚠️ <b>LOW MARGIN</b> ({delta / owed * 100:.1f}%)\n"
            f"Reserves: ${reserves_micro / _MICRO:.2f}\n"
            f"Liabilities: ${owed / _MICRO:.2f}\n"
            f"Buffer: ${delta / _MICRO:.2f}\n"
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
