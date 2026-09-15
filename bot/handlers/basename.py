"""Basename (Base name service) handler — on-chain identity.

/basename              → show the user's Basename (reverse resolution of
                         their deposit address, ENSIP-19 on the L2 resolver)
/basename <name.base.eth> → on-chain availability + ownership check:
                         free / taken (owner address) / confirmed-mine

All lookups go straight to Base contracts (bot/base.py wraps
bot/chain/basenames.py); nothing is stored — the chain is the source of
truth, and display elsewhere (e.g. /wallet) uses the same reverse lookup.
"""
import asyncio

from aiogram import F, types
from aiogram.filters import Command

from bot import i18n

from . import _common as common


def _clean(name: str) -> str:
    return (name or "").strip().lstrip("@").lower()


async def _my_basename_text(tg_id: int, lang: str) -> str:
    from bot.tip_targets import display_name_for
    name = await display_name_for(tg_id)
    if name:
        return i18n.t(lang, 'basename_mine', name=common._esc(name))
    return i18n.t(lang, 'basename_none')


@common.router.callback_query(F.data == 'basename')
async def cb_basename(cb: types.CallbackQuery) -> None:
    """Menu button: show my on-chain name (same answer as /basename)."""
    lang = await common.user_lang(cb.from_user.id)
    await cb.message.edit_text(await _my_basename_text(cb.from_user.id, lang))
    await cb.answer()


@common.router.message(Command('basename'))
async def cmd_basename(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()

    # /basename — show my on-chain name
    if len(parts) == 1:
        await message.answer(await _my_basename_text(message.from_user.id, lang))
        return

    # /basename <name.base.eth> — availability / ownership
    name = _clean(parts[1])
    if not common.base.is_basename(name):
        await message.answer(i18n.t(lang, 'basename_invalid'))
        return

    free = await asyncio.to_thread(common.base.basename_available_sync, name)
    if free is None:
        await message.answer(i18n.t(lang, 'basename_rpc_error'))
        return
    if free:
        await message.answer(i18n.t(lang, 'basename_free', name=common._esc(name)))
        return

    # Taken — does it belong to the caller's own wallet?
    owner = await asyncio.to_thread(common.base.resolve_basename_sync, name)
    if owner is None:
        await message.answer(i18n.t(lang, 'basename_rpc_error'))
        return
    mine = owner.lower() in {
        a.lower() for a in (
            await common.ledger.linked_address(message.from_user.id),
            await common.ledger.wallet_address(message.from_user.id),
        ) if a
    }
    if mine:
        await message.answer(i18n.t(lang, 'basename_owned', name=common._esc(name)))
    else:
        await message.answer(i18n.t(lang, 'basename_taken', name=common._esc(name), addr=common._esc(owner)))
