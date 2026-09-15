"""Валидация параметров MCP tools — защита от STOMP selector injection."""

from __future__ import annotations

import re

from mcp_server.errors import IssPlusValidationError

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INTERVALS = {1, 10, 60, 24, 7, 31, 4}
TICKER_RE = re.compile(r"^[A-Za-z0-9]{1,15}$")
# Поиск по названию: буквы (лат/кириллица), цифры, пробел, точка, дефис, подчёркивание.
QUERY_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 ._-]{1,80}$")


def validate_optional_date(value: str | None) -> str | None:
    """Дата YYYY-MM-DD или пусто."""

    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if not DATE_RE.fullmatch(text):
        raise IssPlusValidationError("Дата должна быть в формате YYYY-MM-DD")
    return text


def validate_interval(interval: int) -> int:
    """Допустимые интервалы свечей ISS."""

    try:
        value = int(interval)
    except (TypeError, ValueError) as exc:
        raise IssPlusValidationError("interval должен быть числом") from exc
    if value not in INTERVALS:
        raise IssPlusValidationError("interval: 1, 10, 60 (мин) или 24, 7, 31, 4 (дни/неделя/месяц/квартал)")
    return value


DESTINATION_RE = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")
# Для subscribe_raw: без переводов строк и STOMP-разделителей.
SELECTOR_FORBIDDEN_RE = re.compile(r"[\x00\n\r:]")


def validate_ticker(ticker: str) -> str:
    """Нормализовать и проверить тикер (только буквы/цифры)."""

    value = (ticker or "").strip().upper()
    if not TICKER_RE.fullmatch(value):
        raise IssPlusValidationError(
            "Некорректный тикер: допустимы только латинские буквы и цифры, длина 1–15"
        )
    return value


def validate_secid(ticker: str) -> str:
    """Проверить код инструмента ISS, сохраняя регистр (фьючерсы вроде SiH6)."""

    value = (ticker or "").strip()
    if not TICKER_RE.fullmatch(value):
        raise IssPlusValidationError(
            "Некорректный тикер: допустимы только латинские буквы и цифры, длина 1–15"
        )
    return value


def validate_query(query: str) -> str:
    """Проверить поисковую строку (без кавычек и спецсимволов selector)."""

    value = (query or "").strip()
    if not value or not QUERY_RE.fullmatch(value):
        raise IssPlusValidationError(
            "Некорректный поисковый запрос: уберите кавычки и спецсимволы"
        )
    return value


def validate_destination(destination: str) -> str:
    """Проверить destination ISS+."""

    value = (destination or "").strip()
    if not DESTINATION_RE.fullmatch(value):
        raise IssPlusValidationError("Некорректный destination")
    return value


def validate_selector(selector: str) -> str:
    """Проверить произвольный selector: без NUL/переводов строк/двоеточий заголовков."""

    value = (selector or "").strip()
    if not value or len(value) > 200:
        raise IssPlusValidationError("Некорректный selector")
    if SELECTOR_FORBIDDEN_RE.search(value):
        raise IssPlusValidationError("Selector содержит запрещённые символы")
    return value


def validate_timeout(timeout_sec: int, *, default: int = 5, maximum: int = 30) -> int:
    """Ограничить таймаут ожидания ответа ISS+."""

    try:
        value = int(timeout_sec)
    except (TypeError, ValueError) as exc:
        raise IssPlusValidationError("timeout_sec должен быть целым числом") from exc
    if value < 1 or value > maximum:
        raise IssPlusValidationError(f"timeout_sec должен быть от 1 до {maximum}")
    return value


def security_selector(ticker: str) -> str:
    """Selector снепшота бумаги на TQBR.

    Формат взят из задания; публичная документация ISS+ по LANGUAGE
    неоднозначна — параметр передаём как указано в спецификации проекта.
    """

    safe = validate_ticker(ticker)
    return f'TICKER="MXSE.TQBR.{safe}" and LANGUAGE="ru"'


def orderbook_selector(ticker: str) -> str:
    """Selector стакана TQBR."""

    safe = validate_ticker(ticker)
    return f'TICKER="MXSE.TQBR.{safe}"'


def search_selector(query: str) -> str:
    """Selector поиска тикера."""

    safe = validate_query(query)
    return f'pattern="{safe}"'
