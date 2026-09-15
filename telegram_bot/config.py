"""Конфигурация Telegram-бота из переменных окружения."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    """Настройки бота, OpenAI и клиента MCP."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"

    mcp_server_url: str = "http://127.0.0.1:8765"
    mcp_api_key: str = Field(default="")

    log_level: str = "INFO"
    log_dir: Path = Path("logs")

    bot_rate_limit_per_minute: int = 10
    bot_max_message_chars: int = 1500

    @property
    def mcp_endpoint(self) -> str:
        """URL Streamable HTTP endpoint (…/mcp)."""

        base = (self.mcp_server_url or "").rstrip("/")
        if base.endswith("/mcp"):
            return base
        return f"{base}/mcp"


@lru_cache(maxsize=1)
def get_settings() -> BotSettings:
    """Вернуть закэшированные настройки бота."""

    return BotSettings()
