"""Обработчики aiogram: /start, /help и текстовые сообщения."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from telegram_bot.config import BotSettings
from telegram_bot.llm_router import LlmRouter
from telegram_bot.prompts import (
    HELP_MESSAGE,
    RATE_LIMIT_MESSAGE,
    START_MESSAGE,
    TOO_LONG_MESSAGE,
    USER_FACING_ERROR,
)
from telegram_bot.rate_limit import RateLimiter
from telegram_bot.sanitize import sanitize_user_text

log = logging.getLogger("telegram.bot")
error_log = logging.getLogger("telegram.errors")

router = Router()


class BotRuntime:
    """Зависимости хендлеров, подставляемые при старте (и в тестах)."""

    def __init__(
        self,
        settings: BotSettings,
        router_llm: LlmRouter,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.router_llm = router_llm
        self.limiter = limiter or RateLimiter(settings.bot_rate_limit_per_minute, 60.0)


_runtime: BotRuntime | None = None


def set_runtime(runtime: BotRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def get_runtime() -> BotRuntime:
    if _runtime is None:
        raise RuntimeError("Bot runtime is not initialized")
    return _runtime


async def process_text_message(user_id: int, chat_id: int, text: str) -> str:
    """Бизнес-логика текстового сообщения (удобно тестировать без Telegram)."""

    runtime = get_runtime()
    preview = (text or "").replace("\n", " ")[:80]
    log.info("inbound user_id=%s chat_id=%s text=%s", user_id, chat_id, preview)
    if not runtime.limiter.allow(user_id):
        log.info("rate_limited user_id=%s", user_id)
        return RATE_LIMIT_MESSAGE
    cleaned = sanitize_user_text(text, runtime.settings.bot_max_message_chars)
    if not cleaned:
        return HELP_MESSAGE
    if len(text or "") > runtime.settings.bot_max_message_chars:
        return TOO_LONG_MESSAGE
    try:
        result = await runtime.router_llm.handle_user_message(cleaned)
    except Exception:
        error_log.exception("handler_failed user_id=%s chat_id=%s", user_id, chat_id)
        return USER_FACING_ERROR
    log.info(
        "outbound user_id=%s used_tool=%s fallback=%s",
        user_id,
        result.used_tool,
        result.fallback,
    )
    return result.text


@router.message(CommandStart())
async def cmd_start(message: Message) -> Any:
    await message.answer(START_MESSAGE)


@router.message(Command("help"))
async def cmd_help(message: Message) -> Any:
    await message.answer(HELP_MESSAGE)


@router.message()
async def on_text(message: Message) -> Any:
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id if message.chat else 0
    text = message.text or message.caption or ""
    try:
        reply = await process_text_message(user_id, chat_id, text)
    except Exception:
        error_log.exception("unhandled_handler")
        reply = USER_FACING_ERROR
    await message.answer(reply)
