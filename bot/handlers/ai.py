"""/ask — AI assistant handler (OpenAI-compatible backend, see bot/ai.py)."""
import html

from aiogram import types
from aiogram.filters import Command
from aiogram.types import BotCommand

from .. import ai, i18n
from . import _common as common

__all__ = ['AI_BOT_COMMAND', 'cmd_ask']
AI_BOT_COMMAND = BotCommand(command='ask', description=i18n.t('en', 'ai_bot_cmd'))

def _quote_context(message: types.Message) -> str:
    ref = getattr(message, 'reply_to_message', None)
    if not ref or not ref.text:
        return ''
    snippet = ref.text[:800]
    return f'\n\nContext (replied message):\n{snippet}'

@common.router.message(Command('ask'))
async def cmd_ask(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    question = message.text.split(maxsplit=1)[1].strip() if len(message.text.split(maxsplit=1)) > 1 else ''
    if not question:
        await message.answer(i18n.t(lang, 'ai_question_empty'))
        return
    if not ai.ai_enabled():
        await message.answer(i18n.t(lang, 'ai_disabled'))
        return
    if len(question) > common.config.AI_MAX_QUESTION_LEN:
        await message.answer(i18n.t(lang, 'ai_question_long', n=common.config.AI_MAX_QUESTION_LEN))
        return
    wait = await common._throttle(message.from_user.id, 'ask')
    if wait:
        await message.answer(wait)
        return
    full_question = question + _quote_context(message)
    try:
        await message.bot.send_chat_action(message.chat.id, 'typing')
    except Exception:
        pass
    try:
        answer = await ai.ask_about_markets(full_question)
    except RuntimeError as e:
        await message.answer(i18n.t(lang, 'ai_error', error=html.escape(str(e))))
        return
    if not answer:
        await message.answer(i18n.t(lang, 'ai_empty_answer'))
        return
    if len(answer) > common.config.AI_MAX_ANSWER_CHARS:
        answer = answer[:common.config.AI_MAX_ANSWER_CHARS - 1] + '…'
    header = '🧠 <b>Tippy AI</b>\n\n'
    await message.answer(header + html.escape(answer)[:4096 - len(header)])
