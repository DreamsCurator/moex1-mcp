"""Санитизация пользовательского ввода и исходящих ответов."""

from __future__ import annotations

import re

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_user_text(text: str, max_chars: int) -> str:
    """Обрезать и убрать управляющие символы (кроме перевода строки)."""

    cleaned = CONTROL_RE.sub("", text or "").strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    return cleaned


def redact_secrets(text: str, secrets: list[str]) -> str:
    """Заменить известные секреты на ***."""

    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return result
