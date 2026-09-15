"""Логирование MCP-сервера: JSON-строки, ротация, редактирование секретов."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from mcp_server.config import McpServerSettings

SECRET_ENV_FIELDS = (
    "moex_passcode",
    "moex_algopack_token",
    "mcp_api_key",
    "moex_login",
)


class RedactingJsonFormatter(logging.Formatter):
    """Форматтер JSON с вырезанием известных секретов из текста записи."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]
        escaped = [re.escape(s) for s in self._secrets]
        self._pattern = re.compile("|".join(escaped)) if escaped else None

    def _redact(self, text: str) -> str:
        if not self._pattern:
            return text
        return self._pattern.sub("***", text)

    def format(self, record: logging.LogRecord) -> str:
        message = self._redact(record.getMessage())
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
        }
        if record.exc_info:
            payload["exc"] = self._redact(self.formatException(record.exc_info))
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def _collect_secrets(settings: McpServerSettings) -> list[str]:
    values: list[str] = []
    for field in SECRET_ENV_FIELDS:
        raw = getattr(settings, field, "") or ""
        if raw.strip():
            values.append(raw.strip())
            if ":" in raw:
                values.extend(part for part in raw.split(":", 1) if part)
    return values


def setup_logging(settings: McpServerSettings) -> None:
    """Настроить корневые логгеры сервера с файловой ротацией."""

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingJsonFormatter(_collect_secrets(settings))

    file_handler = RotatingFileHandler(
        log_dir / "mcp_server.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    level = getattr(logging, settings.log_level, logging.INFO)
    for name in (
        "moex.websocket",
        "moex.mcp.tools",
        "moex.errors",
        "moex.http",
        "mcp_server",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
    ):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.handlers.clear()
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        logger.propagate = False
