"""Тесты обработчиков бота без реального Telegram."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from telegram_bot.config import BotSettings
from telegram_bot.handlers import BotRuntime, process_text_message, set_runtime
from telegram_bot.llm_router import RouteResult
from telegram_bot.prompts import (
    FALLBACK_MESSAGE,
    HELP_MESSAGE,
    RATE_LIMIT_MESSAGE,
    START_MESSAGE,
    TOO_LONG_MESSAGE,
)
from telegram_bot.rate_limit import RateLimiter


class FakeRouter:
    def __init__(self, result: RouteResult | None = None, error: Exception | None = None) -> None:
        self.result = result or RouteResult(text="цена 250", used_tool=True, tool_name="get_security_snapshot")
        self.error = error
        self.seen: list[str] = []

    async def handle_user_message(self, user_text: str) -> RouteResult:
        self.seen.append(user_text)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def runtime() -> BotRuntime:
    settings = BotSettings(
        telegram_bot_token="123456:TESTTOKEN",
        openai_api_key="sk-test-openai-key",
        mcp_api_key="test-mcp-key",
        bot_rate_limit_per_minute=3,
        bot_max_message_chars=100,
    )
    rt = BotRuntime(settings, FakeRouter(), RateLimiter(3, 60))  # type: ignore[arg-type]
    set_runtime(rt)
    yield rt
    set_runtime(None)


@pytest.mark.asyncio
async def test_process_text_ok(runtime: BotRuntime) -> None:
    text = await process_text_message(1, 2, "Какая цена SBER?")
    assert "250" in text


@pytest.mark.asyncio
async def test_fallback_passthrough(runtime: BotRuntime) -> None:
    runtime.router_llm.result = RouteResult(text=FALLBACK_MESSAGE, used_tool=False, fallback=True)  # type: ignore[attr-defined]
    text = await process_text_message(1, 2, "Какая погода в Москве?")
    assert text == FALLBACK_MESSAGE


@pytest.mark.asyncio
async def test_rate_limit(runtime: BotRuntime) -> None:
    for _ in range(3):
        await process_text_message(42, 1, "SBER")
    text = await process_text_message(42, 1, "SBER")
    assert text == RATE_LIMIT_MESSAGE


@pytest.mark.asyncio
async def test_too_long(runtime: BotRuntime) -> None:
    text = await process_text_message(1, 1, "x" * 200)
    assert text == TOO_LONG_MESSAGE


@pytest.mark.asyncio
async def test_handler_exception_is_friendly(runtime: BotRuntime) -> None:
    runtime.router_llm.error = RuntimeError("/secret/path/traceback")  # type: ignore[attr-defined]
    text = await process_text_message(1, 1, "SBER")
    assert "traceback" not in text.lower()
    assert "secret/path" not in text
    assert "поддерж" in text.lower() or "позже" in text.lower()


def test_start_and_help_copy() -> None:
    assert "Сбербанка" in START_MESSAGE
    assert "Лукойл" in HELP_MESSAGE
