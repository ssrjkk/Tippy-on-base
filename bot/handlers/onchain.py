"""On-chain markets (OutcomeMarket.sol) — Polymarket-style trading on Base.

Unlike the off-chain LMSR markets in ledger.py (instant internal accounting),
these trades are REAL on-chain transactions signed by the user's own wallet:
shares are ERC-1155 tokens, USDC moves contract-to-contract, resolution
payouts are pulled from the contract by anyone — fully auditable on
Basescan. The bot only co-ordinates: it drips gas (rate-limited), keeps the
human-readable labels in a Postgres registry (the contract stores numbers
only) and builds/signs from the user's encrypted custodial wallet key.

Prerequisites: OUTCOME_MARKET_ADDRESS set + deployed OutcomeMarket.sol; the
user funds their personal wallet with USDC (`/withdraw <адрес> <сумма>`).
"""
import asyncio
import html
import json
import logging
import time
from decimal import Decimal

from aiogram import F, types
from aiogram.filters import Command

from bot import i18n

from .. import onchain_market as om
from ..ledger import lmsr_buy_shares, lmsr_sell_value
from . import _common as common
from .wallet import _ensure_wallet

__all__ = ['cb_oc_resolve', 'cmd_oc', 'cmd_oc_buy', 'cmd_oc_create', 'cmd_oc_pos', 'cmd_oc_redeem', 'cmd_oc_resolve', 'cmd_oc_sell', 'onchain_watcher']

ZERO_ADDR = '0x0000000000000000000000000000000000000000'
SELL_SLIPPAGE = Decimal('0.99')  # accept 1% slippage between quote and mine
MAX_ONCHAIN_OUTCOMES = 8
# The contract lets anyone cancel at close + 24h; the bot waits one extra
# hour before auto-cancelling to give the creator a last chance to resolve.
ONCHAIN_CANCEL_GRACE_SECONDS = 24 * 3600 + 3600

log = logging.getLogger("tipbot.onchain")


def esc(s: str) -> str:
    return html.escape(str(s))


async def _wallet_key(tg_id: int) -> tuple[str, str]:
    """(address, private_key) of the user's active wallet. The key NEVER
    leaves this function's scope — it is only ever handed to the signer."""
    row = await common.ledger.get_active_wallet(tg_id)
    if not row:
        row = await _ensure_wallet(tg_id)
    key = common.wallets.decrypt(row['key_enc'])
    return row['address'], key


def _wallet_usdc_sync(address: str) -> int:
    w3 = om._w3()
    return om._usdc_contract(w3).functions.balanceOf(
        om.Web3.to_checksum_address(address)
    ).call()


def _q_sync(market_id: int, n: int) -> list[int]:
    """Per-outcome outstanding supply (micro-shares), read from the chain.

    ERC1155Supply.totalSupply — NOT balanceOf(zero): minting credits
    traders, so the zero-address balance stays zero and the local LMSR
    estimate would price every market as if its book were empty.
    """
    c = om._market_contract(om._w3())
    return [c.functions.totalSupply(market_id * 256 + i).call() for i in range(n)]


def _b_sync(market_id: int) -> int:
    m = om._market_contract(om._w3()).functions.markets(market_id).call()
    return int(m[4])


async def _prices(market_id: int, n: int) -> list[Decimal]:
    """Live LMSR prices per outcome via the contract's priceOf view."""
    return await om.market_prices(market_id, n)


def _tx_link(tx_hash: str) -> str:
    return f"{common.config.BASESCAN_URL}/tx/{tx_hash}"


async def _trade_keyboard(mid: int, options: list[str], tg_id: int | None) -> types.InlineKeyboardMarkup | None:
    """One-tap trading on the market card: a buy button per outcome plus
    sell buttons for the outcomes the viewer personally holds."""
    rows: list[list[types.InlineKeyboardButton]] = []
    try:
        info = await om.get_market_info(mid)
        tradable = not info['resolved'] and not info['cancelled']
    except Exception:
        tradable = False
    if tradable:
        for i, o in enumerate(options):
            rows.append([types.InlineKeyboardButton(
                text=f"▲ {i + 1}) {o[:22]}", callback_data=f"ocbuy:{mid}:{i}")])
    if tg_id is not None:
        try:
            w = await common.ledger.get_active_wallet(tg_id)
            if w:
                addr = w['address']

                def _bals():
                    c = om._market_contract(om._w3())
                    cs = om.Web3.to_checksum_address(addr)
                    return [c.functions.balanceOf(cs, mid * 256 + i).call() for i in range(len(options))]

                bals = await asyncio.to_thread(_bals)
                for i, bal in enumerate(bals):
                    if bal > 0:
                        rows.append([types.InlineKeyboardButton(
                            text=f"▼ sell {options[i][:20]}", callback_data=f"ocsell:{mid}:{i}")])
        except Exception:
            pass  # the card must render even if the balance probe fails
    return types.InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _card(m: dict, prices: list[Decimal], lang: str) -> str:
    options = json.loads(m['options'])
    lines = [f"⛓️ #{m['id']} <b>{esc(m['question'])}</b>"]
    lines.append(i18n.t(lang, 'oc_deadline', dt=time.strftime('%d.%m %H:%M', time.localtime(m['close_at']))))
    for i, o in enumerate(options):
        pct = int((prices[i] * 100).to_integral_value()) if i < len(prices) else 0
        lines.append(f"{i + 1}) {esc(o)} — <b>{pct}%</b>")
    return '\n'.join(lines)


@common.router.message(Command('oc'))
async def cmd_oc(message: types.Message) -> None:
    """Overview: /oc — list on-chain markets; /oc <id> — market card."""
    lang = await common.user_lang(message.from_user.id)
    if not common.config.OUTCOME_MARKET_ADDRESS:
        await message.answer(i18n.t(lang, 'oc_disabled'))
        return
    parts = message.text.strip().split()
    if len(parts) == 2 and common.BET_ID_RE.match(parts[1]):
        mid = int(parts[1])
        m = await common.ledger.get_onchain_market(mid)
        if not m:
            await message.answer(i18n.t(lang, 'oc_unknown'))
            return
        options = json.loads(m['options'])
        prices = await _prices(mid, len(options))
        kb = await _trade_keyboard(mid, options, message.from_user.id)
        await message.answer(_card(m, prices, lang) + '\n\n' + i18n.t(lang, 'oc_hint', id=mid), reply_markup=kb)
        return
    rows = await common.ledger.list_onchain_markets(10)

    async def _pcts(m) -> str:
        try:
            prices = await _prices(int(m['id']), len(json.loads(m['options'])))
            return '/'.join(f"{int((p * 100).to_integral_value())}%" for p in prices)
        except Exception:
            return '—'

    # All markets' prices fetched concurrently: N sequential RPC round trips
    # would make /oc feel dead on a slow provider.
    pcts_list = await asyncio.gather(*(_pcts(m) for m in rows))
    lines = [i18n.t(lang, 'oc_list_header')]
    if not rows:
        lines.append(i18n.t(lang, 'oc_list_empty'))
    for m, pcts in zip(rows, pcts_list):
        lines.append(f"⛓️ #{m['id']} {esc(m['question'])} — {pcts}")
    lines.append('\n' + i18n.t(lang, 'oc_help'))
    await message.answer('\n'.join(lines))


@common.router.message(Command('oc_create'))
async def cmd_oc_create(message: types.Message) -> None:
    """Create an ON-CHAIN market: /oc_create 50 Question | Opt1 | Opt2 [24h].

    The subsidy is locked in the contract from the creator's personal wallet.
    """
    lang = await common.user_lang(message.from_user.id)
    if not common.config.OUTCOME_MARKET_ADDRESS:
        await message.answer(i18n.t(lang, 'oc_disabled'))
        return
    body = ' '.join(message.text.strip().split()[1:])
    segs = [s.strip() for s in body.split('|') if s.strip()]
    if not segs:
        await message.answer(i18n.t(lang, 'oc_format_create'))
        return
    head = segs[0].split(None, 1)
    if len(head) != 2 or not common.AMOUNT_RE.match(head[0]) or len(segs) < 3:
        await message.answer(i18n.t(lang, 'oc_format_create'))
        return
    try:
        subsidy = common._to_micro(Decimal(head[0]))
    except Exception:
        await message.answer(i18n.t(lang, 'oc_format_create'))
        return
    if subsidy < common._to_micro(common.config.MARKET_MIN_SUBSIDY_USDC):
        await message.answer(i18n.t(lang, 'market_min_bank', n=f'{common.config.MARKET_MIN_SUBSIDY_USDC:.0f}'))
        return
    if subsidy > common._to_micro(common.config.MARKET_MAX_SUBSIDY_USDC):
        await message.answer(i18n.t(lang, 'market_max_bank', n=f'{common.config.MARKET_MAX_SUBSIDY_USDC:.0f}'))
        return
    daily_cap = common._to_micro(common.config.MARKET_SUBSIDY_DAILY_MAX_USDC)
    if not await common.ledger.try_book_subsidy(subsidy, daily_cap):
        await message.answer(i18n.t(lang, 'oc_subsidy_cap', amount=f'{common.config.MARKET_SUBSIDY_DAILY_MAX_USDC:.0f}'))
        return
    question = head[1].strip()
    options = segs[1:][:MAX_ONCHAIN_OUTCOMES]
    close_at = int(time.time()) + 7 * 86400
    if options and common.DEADLINE_RE.match(options[-1].lower()):
        m = common.DEADLINE_RE.match(options[-1].lower())
        secs = int(m.group(1)) * (3600 if m.group(2) == 'h' else 86400)
        close_at = int(time.time()) + min(secs, 365 * 86400)
        options = options[:-1]
    if len(options) < 2 or len(question) > 200 or any(len(o) > common.config.MAX_OPTION_LEN for o in options):
        await message.answer(i18n.t(lang, 'oc_format_create'))
        return
    wait = await common._throttle(message.from_user.id, 'oc')
    if wait:
        await message.answer(wait)
        return
    tg_id = message.from_user.id
    await common.ledger.ensure_user(tg_id, message.from_user.username)
    try:
        addr, key = await _wallet_key(tg_id)
    except Exception:
        await message.answer(i18n.t(lang, 'oc_wallet_error'))
        return
    status = await message.answer(i18n.t(lang, 'oc_pending'))
    try:
        market_id = await om.create_market(len(options), subsidy, close_at, key)
    except Exception as e:
        await status.edit_text(i18n.t(lang, 'oc_tx_failed', err=str(e)[:200]))
        return
    try:
        await common.ledger.save_onchain_market(market_id, tg_id, question, options, close_at)
        registry_note = ''
    except Exception as e:
        # The market is real on-chain; never let a DB hiccup hide it from
        # the user — tell them it exists and how to re-register it.
        log.warning('onchain registry save failed for #%s: %s', market_id, e)
        registry_note = '\n⚠️ ' + i18n.t(lang, 'oc_registry_warn', err=str(e)[:120])
    await status.edit_text(i18n.t(lang, 'oc_created', id=market_id, q=esc(question), addr=addr) + registry_note)


async def _buy_core(tg_id: int, mid: int, outcome: int, spend: int, lang: str) -> tuple[bool, str]:
    """Shared buy flow for the /oc_buy command and the one-tap buttons."""
    m = await common.ledger.get_onchain_market(mid)
    if not m or outcome < 0 or outcome >= len(json.loads(m['options'])):
        return False, i18n.t(lang, 'oc_unknown')
    if m['close_at'] and int(time.time()) > m['close_at']:
        return False, i18n.t(lang, 'market_trade_deadline')
    wait = await common._throttle(tg_id, 'oc')
    if wait:
        return False, wait
    addr, key = await _wallet_key(tg_id)
    usdc_micro = await asyncio.to_thread(_wallet_usdc_sync, addr)
    if usdc_micro < spend:
        return False, i18n.t(lang, 'oc_need_funds', addr=addr, have=common._fmt(usdc_micro))
    options = json.loads(m['options'])
    q = await asyncio.to_thread(_q_sync, mid, len(options))
    b_micro = await asyncio.to_thread(_b_sync, mid)
    # Local LMSR estimate from live on-chain quantities; the tx itself carries
    # the hard slippage cap `spend`, so estimate error is only cosmetic.
    shares = lmsr_buy_shares(list(q), b_micro, outcome, spend)
    if shares <= 0:
        return False, i18n.t(lang, 'market_trade_toosmall')
    try:
        tx_hash = await om.buy(mid, outcome, shares, spend, key)
    except Exception as e:
        return False, i18n.t(lang, 'oc_tx_failed', err=str(e)[:200])
    try:
        # A failed trade-log write must never eat the trade confirmation:
        # the on-chain tx is already real and the user must see its result.
        await common.ledger.record_onchain_trade(mid, tg_id, outcome, shares, tx_hash)
    except Exception as e:
        log.warning('onchain trade log failed for #%s: %s', mid, e)
    return True, i18n.t(lang, 'oc_bought', label=esc(options[outcome]), shares=common._fmt(shares), cost=common._fmt(spend), url=_tx_link(tx_hash))


async def _sell_core(tg_id: int, mid: int, outcome: int, pct: int, lang: str) -> tuple[bool, str]:
    """Shared sell flow for the /oc_sell command and the buttons."""
    m = await common.ledger.get_onchain_market(mid)
    if not m or outcome < 0 or outcome >= len(json.loads(m['options'])):
        return False, i18n.t(lang, 'oc_unknown')
    wait = await common._throttle(tg_id, 'oc')
    if wait:
        return False, wait
    addr, key = await _wallet_key(tg_id)

    def _held():
        c = om._market_contract(om._w3())
        return c.functions.balanceOf(om.Web3.to_checksum_address(addr), mid * 256 + outcome).call()

    held = await asyncio.to_thread(_held)
    if held <= 0:
        return False, i18n.t(lang, 'oc_no_shares')
    shares = held * pct // 100
    options = json.loads(m['options'])
    q = await asyncio.to_thread(_q_sync, mid, len(options))
    b_micro = await asyncio.to_thread(_b_sync, mid)
    value = lmsr_sell_value(list(q), b_micro, outcome, shares)
    if value <= 0:
        return False, i18n.t(lang, 'market_trade_toosmall')
    min_proceeds = int(Decimal(value) * SELL_SLIPPAGE)
    try:
        tx_hash = await om.sell(mid, outcome, shares, min_proceeds, key)
    except Exception as e:
        return False, i18n.t(lang, 'oc_tx_failed', err=str(e)[:200])
    return True, i18n.t(lang, 'oc_sold', label=esc(options[outcome]), shares=common._fmt(shares), value=common._fmt(value), url=_tx_link(tx_hash))


@common.router.message(Command('oc_buy'))
async def cmd_oc_buy(message: types.Message) -> None:
    """/oc_buy <id> <outcome> <amount> — buy shares with the personal wallet."""
    lang = await common.user_lang(message.from_user.id)
    if not common.config.OUTCOME_MARKET_ADDRESS:
        await message.answer(i18n.t(lang, 'oc_disabled'))
        return
    parts = message.text.strip().split()
    if len(parts) != 4 or not common.BET_ID_RE.match(parts[1]) or not common.AMOUNT_RE.match(parts[3]):
        await message.answer(i18n.t(lang, 'oc_format_buy'))
        return
    mid, outcome = int(parts[1]), int(parts[2]) - 1
    try:
        spend = common._to_micro(Decimal(parts[3]))
    except Exception:
        await message.answer(i18n.t(lang, 'oc_format_buy'))
        return
    if spend <= 0 or spend > common._to_micro(common.config.MARKET_MAX_TRADE_USDC):
        await message.answer(i18n.t(lang, 'market_trade_max', n=f'{common.config.MARKET_MAX_TRADE_USDC:.0f}'))
        return
    ok, text = await _buy_core(message.from_user.id, mid, outcome, spend, lang)
    await message.answer(('✅ ' if ok else '') + text)


@common.router.message(Command('oc_sell'))
async def cmd_oc_sell(message: types.Message) -> None:
    """/oc_sell <id> <outcome> [pct] — sell shares back to the AMM."""
    lang = await common.user_lang(message.from_user.id)
    if not common.config.OUTCOME_MARKET_ADDRESS:
        await message.answer(i18n.t(lang, 'oc_disabled'))
        return
    parts = message.text.strip().split()
    if len(parts) not in (3, 4) or not common.BET_ID_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'oc_format_sell'))
        return
    mid, outcome = int(parts[1]), int(parts[2]) - 1
    pct = 100
    if len(parts) == 4:
        raw = parts[3].rstrip('%')
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            await message.answer(i18n.t(lang, 'oc_format_sell'))
            return
        pct = int(raw)
    ok, text = await _sell_core(message.from_user.id, mid, outcome, pct, lang)
    await message.answer(('✅ ' if ok else '') + text)


@common.router.message(Command('oc_redeem'))
async def cmd_oc_redeem(message: types.Message) -> None:
    """/oc_redeem <id> — pull resolution winnings from the contract."""
    lang = await common.user_lang(message.from_user.id)
    if not common.config.OUTCOME_MARKET_ADDRESS:
        await message.answer(i18n.t(lang, 'oc_disabled'))
        return
    parts = message.text.strip().split()
    if len(parts) != 2 or not common.BET_ID_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'oc_format_redeem'))
        return
    mid = int(parts[1])
    if not await common.ledger.get_onchain_market(mid):
        await message.answer(i18n.t(lang, 'oc_unknown'))
        return
    wait = await common._throttle(message.from_user.id, 'oc')
    if wait:
        await message.answer(wait)
        return
    addr, key = await _wallet_key(message.from_user.id)
    status = await message.answer(i18n.t(lang, 'oc_pending'))
    try:
        payout = await om.redeem(mid, key)
    except Exception as e:
        await status.edit_text(i18n.t(lang, 'oc_tx_failed', err=str(e)[:200]))
        return
    await status.edit_text(i18n.t(lang, 'oc_redeemed', amount=common._fmt(payout), addr=addr))


@common.router.message(Command('oc_pos'))
async def cmd_oc_pos(message: types.Message) -> None:
    """/oc_pos — ERC-1155 share positions of the personal wallet."""
    lang = await common.user_lang(message.from_user.id)
    if not common.config.OUTCOME_MARKET_ADDRESS:
        await message.answer(i18n.t(lang, 'oc_disabled'))
        return
    addr, _key = await _wallet_key(message.from_user.id)
    rows = await common.ledger.list_onchain_markets(15)
    lines = [i18n.t(lang, 'oc_pos_header', addr=esc(addr))]

    def _balances(mid: int, n: int) -> list[int]:
        c = om._market_contract(om._w3())
        cs = om.Web3.to_checksum_address(addr)
        return [c.functions.balanceOf(cs, mid * 256 + i).call() for i in range(n)]

    found = False
    for m in rows:
        options = json.loads(m['options'])
        try:
            bals = await asyncio.to_thread(_balances, int(m['id']), len(options))
        except Exception:
            continue
        if not any(bals):
            continue
        found = True
        parts = [f"{common._fmt(v)}× {esc(o)}" for o, v in zip(options, bals) if v > 0]
        lines.append(f"⛓️ #{m['id']} {esc(m['question'])}\n  " + '\n  '.join(parts))
    if not found:
        lines.append(i18n.t(lang, 'oc_pos_empty'))
    await message.answer('\n'.join(lines))


# ---------------------------------------------------------------------
# Resolution: the CREATOR reports the outcome, the bot signs it with the
# oracle key (config.ORACLE_PRIVATE_KEY) or — as a fallback — with the
# owner (hot wallet) key. This mirrors Polymarket's UMA flow: a human is
# the resolution source, the on-chain owner keeps the dispute power, and
# anyone can cancel an abandoned market 24h after close.
# ---------------------------------------------------------------------

def _resolve_buttons(mid: int, options: list[str]) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"{i + 1}) {esc(o[:30])}", callback_data=f"ocr:{mid}:{i}")]
        for i, o in enumerate(options)
    ])


async def _do_resolve(mid: int, winner_idx: int, lang: str, bot=None) -> tuple[bool, str]:
    """Sign and broadcast the on-chain resolution. Returns (ok, text)."""
    info = await om.get_market_info(mid)
    if info["resolved"]:
        # Chain already settled (someone else resolved); sync our registry.
        await common.ledger.set_onchain_resolved(mid, int(info["winning_outcome"]))
        return False, i18n.t(lang, "oc_resolve_state", state="resolved")
    if info["cancelled"]:
        await common.ledger.mark_onchain_cancelled(mid)
        return False, i18n.t(lang, "oc_resolve_state", state="cancelled")
    oracle_key = common.config.ORACLE_PRIVATE_KEY.strip()
    try:
        if oracle_key:
            tx_hash = await om.oracle_resolve(mid, winner_idx, oracle_key)
        else:
            tx_hash = await om.owner_resolve(mid, winner_idx, common.config.HOT_WALLET_KEY)
    except Exception as e:
        err = str(e)
        if "MarketDisputed" in err:
            # The oracle answer was disputed: only the contract owner can
            # finalize now. Point the creator there instead of a raw revert.
            return False, i18n.t(lang, "oc_resolve_disputed")
        return False, i18n.t(lang, "oc_tx_failed", err=err[:200])
    await common.ledger.set_onchain_resolved(mid, winner_idx)
    await _notify_winners(mid, winner_idx, bot)
    return True, i18n.t(lang, "oc_resolved", idx=winner_idx + 1, url=_tx_link(tx_hash))


async def _notify_winners(mid: int, winner_idx: int, bot=None) -> None:
    """DM everyone we know bought the winning outcome. Holdings live in
    ERC-1155 — a buyer who sold since gets a zero payout, and /oc_redeem
    turns that into a clean NothingToRedeem error."""
    if bot is None:
        return
    try:
        m = await common.ledger.get_onchain_market(mid)
        label = json.loads(m["options"])[winner_idx] if m else f"#{winner_idx}"
        for r in await common.ledger.onchain_trades_for_outcome(mid, winner_idx):
            try:
                wl = await common.user_lang(int(r["tg_id"]))
                await bot.send_message(int(r["tg_id"]), i18n.t(
                    wl, 'oc_won_dm', id=mid,
                    label=esc(label), shares=common._fmt(int(r["shares"]))))
            except Exception:
                pass  # recipient blocked the bot — never block the resolve path
    except Exception as e:
        log.warning('winner notify failed for #%s: %s', mid, e)


@common.router.message(Command('oc_resolve'))
async def cmd_oc_resolve(message: types.Message) -> None:
    """/oc_resolve <id> <winner> — creator reports the outcome (ON-CHAIN)."""
    lang = await common.user_lang(message.from_user.id)
    if not common.config.OUTCOME_MARKET_ADDRESS:
        await message.answer(i18n.t(lang, 'oc_disabled'))
        return
    parts = message.text.strip().split()
    if len(parts) != 3 or not common.BET_ID_RE.match(parts[1]) or not parts[2].isdigit():
        await message.answer(i18n.t(lang, 'oc_resolve_format'))
        return
    mid, winner_idx = int(parts[1]), int(parts[2]) - 1
    m = await common.ledger.get_onchain_market(mid)
    if not m:
        await message.answer(i18n.t(lang, 'oc_unknown'))
        return
    if m['resolved_outcome'] is not None or m['cancelled_flag']:
        state = 'resolved' if m['resolved_outcome'] is not None else 'cancelled'
        await message.answer(i18n.t(lang, 'oc_resolve_state', state=state))
        return
    if int(m['creator']) != message.from_user.id:
        await message.answer(i18n.t(lang, 'oc_resolve_denied'))
        return
    options = json.loads(m['options'])
    if winner_idx < 0 or winner_idx >= len(options):
        await message.answer(i18n.t(lang, 'oc_resolve_format'))
        return
    wait = await common._throttle(message.from_user.id, 'oc')
    if wait:
        await message.answer(wait)
        return
    ok, text = await _do_resolve(mid, winner_idx, lang, bot=message.bot)
    await message.answer(('✅ ' if ok else '❌ ') + text)


@common.router.callback_query(F.data.startswith('ocr:'))
async def cb_oc_resolve(cb: types.CallbackQuery) -> None:
    """Outcome-pick buttons the watcher DMs to the creator after close.

    Ownership is verified against the registry: callback data is
    client-controlled, so a random chat member must never be able to
    resolve someone else's market.
    """
    if not cb.from_user:
        return
    lang = await common.user_lang(cb.from_user.id)
    try:
        _, mid_raw, idx_raw = cb.data.split(':')
        mid, winner_idx = int(mid_raw), int(idx_raw)
    except ValueError:
        await cb.answer()
        return
    m = await common.ledger.get_onchain_market(mid)
    if not m or int(m['creator']) != cb.from_user.id:
        await cb.answer(i18n.t(lang, 'oc_resolve_denied'), show_alert=True)
        return
    options = json.loads(m['options'])
    if winner_idx < 0 or winner_idx >= len(options):
        await cb.answer()
        return
    res_bot = getattr(cb, 'bot', None) or (getattr(cb.message, 'bot', None) if cb.message else None)
    ok, text = await _do_resolve(mid, winner_idx, lang, bot=res_bot)
    await cb.answer(text, show_alert=True)
    if ok:
        try:
            await cb.message.edit_text(f"⛓️ #{mid} — {esc(options[winner_idx])}\n{text}")
        except Exception:
            pass  # message may already be edited or deleted


async def onchain_watcher(bot) -> None:
    """Once per cycle: DM creators of closed on-chain markets with
    outcome-pick buttons, and auto-cancel long-overdue markets so holders
    can pull refunds and the subsidy is not stuck forever."""
    while True:
        try:
            for m in await common.ledger.onchain_markets_past_deadline():
                await common.ledger.mark_onchain_deadline_notified(int(m['id']))
                options = json.loads(m['options'])
                kb = _resolve_buttons(int(m['id']), options)
                try:
                    creator_lang = i18n.norm((await common.ledger.get_settings(int(m['creator']))).get('lang'))
                    await bot.send_message(
                        int(m['creator']),
                        i18n.t(creator_lang, 'oc_pick', id=m['id'], q=esc(m['question'])),
                        reply_markup=kb,
                    )
                except Exception as e:
                    log.warning('onchain deadline notify failed for #%s: %s', m['id'], e)
            for m in await common.ledger.onchain_markets_overdue(ONCHAIN_CANCEL_GRACE_SECONDS):
                try:
                    info = await om.get_market_info(int(m['id']))
                except Exception as e:
                    log.warning('onchain overdue state read failed for #%s: %s', m['id'], e)
                    continue
                if info['resolved']:
                    await common.ledger.set_onchain_resolved(int(m['id']), int(info['winning_outcome']))
                    continue
                if info['cancelled']:
                    await common.ledger.mark_onchain_cancelled(int(m['id']))
                    continue
                try:
                    await om.cancel_expired(int(m['id']), common.config.HOT_WALLET_KEY)
                    await common.ledger.mark_onchain_cancelled(int(m['id']))
                    log.info('onchain market #%s auto-cancelled after expiry', m['id'])
                except Exception as e:
                    log.warning('onchain auto-cancel failed for #%s: %s', m['id'], e)
        except Exception as e:
            log.warning('onchain watcher failed: %s', e)
        await asyncio.sleep(common.config.POLL_SECONDS * 4)


# ---------------------------------------------------------------------
# Two-tap trading: the /oc <id> card carries buy buttons per outcome and
# sell buttons for held outcomes; these callbacks drive them through the
# same _buy_core/_sell_core flows as the text commands.
# ---------------------------------------------------------------------

@common.router.callback_query(F.data.startswith('ocbuy:'))
async def cb_oc_buy(cb: types.CallbackQuery) -> None:
    """Amount picker: tap an outcome on the card, choose USDC, trade."""
    if not cb.from_user:
        return
    lang = await common.user_lang(cb.from_user.id)
    try:
        _, mid_raw, idx_raw = cb.data.split(':')
        mid, outcome = int(mid_raw), int(idx_raw)
    except ValueError:
        await cb.answer()
        return
    m = await common.ledger.get_onchain_market(mid)
    if not m or outcome < 0 or outcome >= len(json.loads(m['options'])):
        await cb.answer(i18n.t(lang, 'oc_unknown'), show_alert=True)
        return
    label = json.loads(m['options'])[outcome]
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text=f"${a}", callback_data=f"ocbuydo:{mid}:{outcome}:{a}")
        for a in common.QUICK_AMOUNTS
    ]])
    try:
        await cb.message.edit_text(i18n.t(lang, 'oc_pick_amount', mid=mid, label=esc(label)), reply_markup=kb)
    except Exception:
        pass  # message may be gone; the trade still goes through on re-tap
    await cb.answer()


@common.router.callback_query(F.data.startswith('ocbuydo:'))
async def cb_oc_buy_do(cb: types.CallbackQuery) -> None:
    if not cb.from_user:
        return
    lang = await common.user_lang(cb.from_user.id)
    try:
        _, mid_raw, idx_raw, amt_raw = cb.data.split(':')
        mid, outcome = int(mid_raw), int(idx_raw)
        if not common.AMOUNT_RE.match(amt_raw):
            raise ValueError
        spend = common._to_micro(Decimal(amt_raw))
    except (ValueError, TypeError):
        await cb.answer()
        return
    if spend <= 0 or spend > common._to_micro(common.config.MARKET_MAX_TRADE_USDC):
        await cb.answer(i18n.t(lang, 'market_trade_max', n=f'{common.config.MARKET_MAX_TRADE_USDC:.0f}'), show_alert=True)
        return
    # Feedback lands in the MESSAGE, not a second cb.answer: Telegram
    # ignores answer() after the first one, so errors would be invisible.
    await cb.answer()
    try:
        await cb.message.edit_text(i18n.t(lang, 'oc_pending'))
    except Exception:
        pass
    ok, text = await _buy_core(cb.from_user.id, mid, outcome, spend, lang)
    try:
        await cb.message.edit_text(('✅ ' if ok else '') + text)
    except Exception:
        pass


@common.router.callback_query(F.data.startswith('ocsell:'))
async def cb_oc_sell(cb: types.CallbackQuery) -> None:
    """Percent picker for a held outcome."""
    if not cb.from_user:
        return
    lang = await common.user_lang(cb.from_user.id)
    try:
        _, mid_raw, idx_raw = cb.data.split(':')
        mid, outcome = int(mid_raw), int(idx_raw)
    except ValueError:
        await cb.answer()
        return
    m = await common.ledger.get_onchain_market(mid)
    if not m or outcome < 0 or outcome >= len(json.loads(m['options'])):
        await cb.answer(i18n.t(lang, 'oc_unknown'), show_alert=True)
        return
    label = json.loads(m['options'])[outcome]
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text=f"{p}%", callback_data=f"ocselldo:{mid}:{outcome}:{p}")
        for p in ('25', '50', '100')
    ]])
    try:
        await cb.message.edit_text(i18n.t(lang, 'oc_pick_pct', mid=mid, label=esc(label)), reply_markup=kb)
    except Exception:
        pass
    await cb.answer()


@common.router.callback_query(F.data.startswith('ocselldo:'))
async def cb_oc_sell_do(cb: types.CallbackQuery) -> None:
    if not cb.from_user:
        return
    lang = await common.user_lang(cb.from_user.id)
    try:
        _, mid_raw, idx_raw, pct_raw = cb.data.split(':')
        mid, outcome, pct = int(mid_raw), int(idx_raw), int(pct_raw)
    except (ValueError, TypeError):
        await cb.answer()
        return
    if not 1 <= pct <= 100:
        await cb.answer()
        return
    await cb.answer()
    try:
        await cb.message.edit_text(i18n.t(lang, 'oc_pending'))
    except Exception:
        pass
    ok, text = await _sell_core(cb.from_user.id, mid, outcome, pct, lang)
    try:
        await cb.message.edit_text(('✅ ' if ok else '') + text)
    except Exception:
        pass
