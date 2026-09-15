"""Запуск Telegram-бота (aiogram polling)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from openai import AsyncOpenAI

from telegram_bot.config import get_settings
from telegram_bot.handlers import BotRuntime, router, set_runtime
from telegram_bot.llm_router import LlmRouter
from telegram_bot.logging_config import collect_bot_secrets, setup_logging
from telegram_bot.mcp_client import McpHttpClient
from telegram_bot.rate_limit import RateLimiter

log = logging.getLogger("telegram.bot")


async def run_bot() -> None:
    """Подключиться к MCP, получить tools, начать polling."""

    settings = get_settings()
    setup_logging(settings)
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не задан")

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    async with McpHttpClient(settings.mcp_endpoint, settings.mcp_api_key) as mcp:
        tools = await mcp.list_tools()
        log.info("bot_start tools=%s model=%s", [t.get("name") for t in tools], settings.openai_model)
        llm = LlmRouter(
            openai_client,
            settings.openai_model,
            tools,
            mcp,
            secrets=collect_bot_secrets(settings),
        )
        set_runtime(
            BotRuntime(
                settings,
                llm,
                RateLimiter(settings.bot_rate_limit_per_minute, 60.0),
            )
        )
        try:
            await dp.start_polling(bot)
        finally:
            set_runtime(None)
            await bot.session.close()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
