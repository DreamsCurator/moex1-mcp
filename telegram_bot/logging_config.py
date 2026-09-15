"""Логирование Telegram-бота: JSON, ротация, редактирование секретов."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from telegram_bot.config import BotSettings

SECRET_FIELDS = (
    "openai_api_key",
    "telegram_bot_token",
    "mcp_api_key",
)


class RedactingJsonFormatter(logging.Formatter):
    """JSON-форматтер, вырезающий известные секреты."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        escaped = [re.escape(s) for s in secrets if s]
        self._pattern = re.compile("|".join(escaped)) if escaped else None

    def _redact(self, text: str) -> str:
        if not self._pattern:
            return text
        return self._pattern.sub("***", text)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": self._redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def collect_bot_secrets(settings: BotSettings) -> list[str]:
    """Список секретных строк, которые нельзя светить в логах."""

    values: list[str] = []
    for field in SECRET_FIELDS:
        raw = getattr(settings, field, "") or ""
        if raw.strip():
            values.append(raw.strip())
    return values


def setup_logging(settings: BotSettings) -> None:
    """Настроить логгеры бота, LLM и ошибок."""

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingJsonFormatter(collect_bot_secrets(settings))
    file_handler = RotatingFileHandler(
        log_dir / "telegram_bot.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    for name in (
        "telegram.bot",
        "telegram.llm",
        "telegram.mcp",
        "telegram.errors",
        "aiogram",
        "telegram_bot",
    ):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.handlers.clear()
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        logger.propagate = False
