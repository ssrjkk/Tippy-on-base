"""/ask — AI assistant handler (OpenAI-compatible backend, see bot/ai.py)."""

import html

from aiogram import types
from aiogram.filters import Command
from aiogram.types import BotCommand

from .. import ai
from . import _common as common

__all__ = ["AI_BOT_COMMAND", "cmd_ask"]

AI_BOT_COMMAND = BotCommand(command="ask", description="Спросить ИИ-ассистента")


def _quote_context(message: types.Message) -> str:
    """If /ask is a reply, include the replied-to text as context."""
    ref = getattr(message, "reply_to_message", None)
    if not ref or not ref.text:
        return ""
    snippet = ref.text[:800]
    return f"\n\nКонтекст (сообщение, на которое ты отвечаешь):\n{snippet}"


@common.router.message(Command("ask"))
async def cmd_ask(message: types.Message) -> None:
    question = message.text.split(maxsplit=1)[1].strip() if len(message.text.split(maxsplit=1)) > 1 else ""
    if not question:
        await message.answer(
            "🧠 Спроси меня о чём угодно: крипта, Base, стратегии для рынков, правила бота.\n"
            "Формат: <code>/ask почему биткоин растёт?</code>\n"
            "Можно ответом на сообщение — возьму его как контекст."
        )
        return
    if not ai.ai_enabled():
        await message.answer(
            "🧠 ИИ-ассистент пока не подключён на этом сервере.\n"
            "Админу: задайте <code>AI_API_KEY</code> (+ опционально <code>AI_API_URL</code>, "
            "<code>AI_MODEL</code>) в .env — подойдёт любой OpenAI-совместимый провайдер."
        )
        return
    if len(question) > common.config.AI_MAX_QUESTION_LEN:
        await message.answer(
            f"Слишком длинный вопрос (макс {common.config.AI_MAX_QUESTION_LEN} символов)."
        )
        return

    wait = common._throttle(message.from_user.id, "ask")
    if wait:
        await message.answer(wait)
        return

    full_question = question + _quote_context(message)
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass
    try:
        answer = ai.ask(full_question)
    except RuntimeError as e:
        await message.answer(f"🤖 ИИ недоступен: <i>{html.escape(str(e))}</i>\nПопробуй позже.")
        return
    if not answer:
        await message.answer("🤖 Пустой ответ от ИИ. Попробуй переформулировать.")
        return
    if len(answer) > common.config.AI_MAX_ANSWER_CHARS:
        answer = answer[: common.config.AI_MAX_ANSWER_CHARS - 1] + "…"
    header = "🧠 <b>Tippy AI</b>\n\n"
    # Telegram caption/text limit is 4096; keep headroom for the header.
    await message.answer(header + html.escape(answer)[: 4096 - len(header)])
