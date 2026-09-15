"""MCP tools для ISS+: snapshot, стакан, поиск тикера, сырая подписка."""

from __future__ import annotations

import logging
import time
from typing import Any

from mcp_server.config import get_settings
from mcp_server.errors import IssPlusError, IssPlusValidationError, USER_FRIENDLY_ISS_ERROR
from mcp_server.moex_ws_client import get_moex_client
from mcp_server.algopack_rest import (
    CALENDAR_PLANNED,
    CALENDAR_SUSPENDED,
    FORTS_BASE,
    FORTS_LIST,
    MAX_ROWS,
    SECURITIES_SEARCH,
    SHARE_BASE,
    extract_tables,
    iss_get,
)
from mcp_server.validation import (
    orderbook_selector,
    search_selector,
    security_selector,
    validate_destination,
    validate_interval,
    validate_optional_date,
    validate_secid,
    validate_selector,
    validate_ticker,
    validate_timeout,
)

log = logging.getLogger("moex.mcp.tools")


def _ok(data: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload = {"ok": True, **data}
    payload.update(extra)
    return payload


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "error": message}


def _tool_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, IssPlusValidationError):
        return _err("validation_error", str(exc))
    if isinstance(exc, IssPlusError):
        return _err(exc.code, USER_FRIENDLY_ISS_ERROR)
    log.exception("tool_unhandled_error type=%s", type(exc).__name__)
    return _err("internal_error", USER_FRIENDLY_ISS_ERROR)


async def get_security_snapshot_impl(ticker: str, timeout_sec: float = 8) -> dict[str, Any]:
    """Снепшот инструмента ``MXSE.securities`` / TQBR, при сбое ISS+ — ISS REST."""

    started = time.perf_counter()
    try:
        safe = validate_ticker(ticker)
        selector = security_selector(safe)
        destination = "MXSE.securities"
        log.info(
            "tool_call name=get_security_snapshot ticker=%s destination=%s",
            safe,
            destination,
        )
        result = await get_moex_client().subscribe_once(
            destination,
            selector,
            timeout_sec=timeout_sec,
            unsubscribe=True,
            command="SUBSCRIBE",
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        rows = result.get("rows") or []
        if rows:
            log.info("tool_done name=get_security_snapshot ticker=%s elapsed_ms=%s", safe, elapsed_ms)
            return _ok(
                {
                    "ticker": safe,
                    "destination": destination,
                    "properties": result.get("properties") or {},
                    "rows": rows,
                }
            )
        log.info("snapshot_empty_ws_fallback ticker=%s", safe)
    except Exception as exc:
        if isinstance(exc, IssPlusValidationError):
            return _tool_error(exc)
        log.info("snapshot_ws_fallback type=%s", type(exc).__name__)
    return await get_share_marketdata_impl(ticker)


async def get_orderbook_impl(ticker: str, timeout_sec: float = 8) -> dict[str, Any]:
    """Текущий стакан: подписка на ``MXSE.orderbooks``, первый snapshot, отписка."""

    started = time.perf_counter()
    try:
        safe = validate_ticker(ticker)
        selector = orderbook_selector(safe)
        destination = "MXSE.orderbooks"
        log.info("tool_call name=get_orderbook ticker=%s destination=%s", safe, destination)
        result = await get_moex_client().subscribe_once(
            destination,
            selector,
            timeout_sec=timeout_sec,
            unsubscribe=True,
            command="SUBSCRIBE",
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info("tool_done name=get_orderbook ticker=%s elapsed_ms=%s", safe, elapsed_ms)
        return _ok(
            {
                "ticker": safe,
                "destination": destination,
                "properties": result.get("properties") or {},
                "rows": result.get("rows") or [],
            }
        )
    except Exception as exc:
        return _tool_error(exc)


async def search_ticker_impl(query: str, timeout_sec: float = 8) -> dict[str, Any]:
    """Поиск: сначала ISS REST, при пустом ответе — REQUEST ``SEARCH.ticker``."""

    from mcp_server.validation import validate_query

    try:
        safe = validate_query(query)
    except IssPlusValidationError as exc:
        return _tool_error(exc)

    rest = await _search_ticker_rest(safe)
    if rest.get("ok") and rest.get("rows"):
        return rest

    started = time.perf_counter()
    try:
        selector = search_selector(safe)
        destination = "SEARCH.ticker"
        log.info("tool_call name=search_ticker destination=%s", destination)
        result = await get_moex_client().subscribe_once(
            destination,
            selector,
            timeout_sec=timeout_sec,
            unsubscribe=False,
            command="REQUEST",
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info("tool_done name=search_ticker elapsed_ms=%s", elapsed_ms)
        return _ok(
            {
                "query": safe,
                "destination": destination,
                "properties": result.get("properties") or {},
                "rows": result.get("rows") or [],
            }
        )
    except Exception as exc:
        if rest.get("ok"):
            return rest
        return _tool_error(exc)


async def _search_ticker_rest(query: str) -> dict[str, Any]:
    """Поиск тикера через публичный ISS REST ``/iss/securities.json``."""

    try:
        payload = await iss_get(
            SECURITIES_SEARCH,
            get_settings(),
            {"q": query, "limit": MAX_ROWS},
        )
        tables = extract_tables(payload)
        rows = tables.get("securities") or []
        if not rows:
            rows = next(iter(tables.values()), []) if tables else []
        tqbr = [
            row
            for row in rows
            if str(row.get("primary_boardid") or row.get("PRIMARY_BOARDID") or "").upper() == "TQBR"
        ]
        return _ok({"query": query, "source": "iss_rest", "rows": (tqbr or rows)[:MAX_ROWS]})
    except Exception as exc:
        return _tool_error(exc)


async def subscribe_raw_impl(
    destination: str,
    selector: str,
    timeout_sec: int = 5,
) -> dict[str, Any]:
    """Низкоуровневая подписка на произвольный destination/selector ISS+."""

    started = time.perf_counter()
    try:
        dest = validate_destination(destination)
        sel = validate_selector(selector)
        timeout = validate_timeout(timeout_sec)
        log.info(
            "tool_call name=subscribe_raw destination=%s timeout_sec=%s",
            dest,
            timeout,
        )
        result = await get_moex_client().subscribe_once(
            dest,
            sel,
            timeout_sec=timeout,
            unsubscribe=True,
            command="SUBSCRIBE",
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info("tool_done name=subscribe_raw destination=%s elapsed_ms=%s", dest, elapsed_ms)
        return _ok(
            {
                "destination": dest,
                "properties": result.get("properties") or {},
                "rows": result.get("rows") or [],
            }
        )
    except Exception as exc:
        return _tool_error(exc)


def register_tools(mcp: Any) -> None:
    """Зарегистрировать MCP tools (ISS+ WebSocket и ISS REST) на FastMCP."""

    @mcp.tool(
        name="get_security_snapshot",
        description=(
            "Текущая цена и параметры акции TQBR. Сначала ISS+ snapshot, "
            "если WebSocket недоступен — ISS REST marketdata (поле LAST). "
            "Для Сбера: SBER обыкн., SBERP привилегированные."
        ),
    )
    async def get_security_snapshot(ticker: str) -> dict[str, Any]:
        """Снепшот бумаги по тикеру TQBR.

        Args:
            ticker: Код инструмента, только латиница и цифры, например SBER.
        """

        return await get_security_snapshot_impl(ticker)

    @mcp.tool(
        name="get_orderbook",
        description=(
            "Получить текущий стакан заявок (order book) по тикеру на TQBR. "
            "Используй, когда пользователь просит стакан, заявки, bid/ask."
        ),
    )
    async def get_orderbook(ticker: str) -> dict[str, Any]:
        """Стакан заявок по тикеру.

        Args:
            ticker: Код инструмента, например GAZP.
        """

        return await get_orderbook_impl(ticker)

    @mcp.tool(
        name="search_ticker",
        description=(
            "Найти тикер по названию компании. Если ищешь цену — после поиска "
            "обязательно вызови get_share_marketdata или get_security_snapshot."
        ),
    )
    async def search_ticker(query: str) -> dict[str, Any]:
        """Поиск тикера по названию.

        Args:
            query: Название или часть названия эмитента/инструмента.
        """

        return await search_ticker_impl(query)

    @mcp.tool(
        name="subscribe_raw",
        description=(
            "Низкоуровневый диагностический запрос к ISS+: произвольные destination "
            "и selector. Используй только если пользователь явно указывает destination "
            "ISS+ или штатные инструменты не покрывают запрос."
        ),
    )
    async def subscribe_raw(
        destination: str,
        selector: str,
        timeout_sec: int = 5,
    ) -> dict[str, Any]:
        """Универсальная подписка ISS+.

        Args:
            destination: Канал ISS+, например MXSE.securities.
            selector: STOMP selector без переводов строк.
            timeout_sec: Таймаут ожидания ответа, 1–30 секунд.
        """

        return await subscribe_raw_impl(destination, selector, timeout_sec)

    @mcp.tool(
        name="get_share_candles",
        description=(
            "Свечи акции TQBR через ISS REST. Для истории цены SBER/GAZP по минутам или дням. "
            "interval: 1/10/60 минут или 24 день."
        ),
    )
    async def get_share_candles(
        ticker: str,
        interval: int = 24,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        return await get_share_candles_impl(ticker, interval, start, end)

    @mcp.tool(
        name="get_share_trades",
        description="Лента сделок акции TQBR (ISS REST). Используй, когда нужны последние сделки.",
    )
    async def get_share_trades(ticker: str) -> dict[str, Any]:
        return await get_share_trades_impl(ticker)

    @mcp.tool(
        name="get_share_marketdata",
        description=(
            "Текущие поля акции TQBR через ISS REST, включая LAST (последняя цена). "
            "Предпочитай этот tool для вопроса «какая сейчас цена». "
            "SBER — обыкн. Сбер, SBERP — привилегированные, ROSN — Роснефть, GAZP, LKOH."
        ),
    )
    async def get_share_marketdata(ticker: str) -> dict[str, Any]:
        return await get_share_marketdata_impl(ticker)

    @mcp.tool(
        name="get_futures_candles",
        description="Свечи фьючерса FORTS/RFUD через ISS REST. Тикер вида SiH6, RIU6.",
    )
    async def get_futures_candles(
        ticker: str,
        interval: int = 24,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        return await get_futures_candles_impl(ticker, interval, start, end)

    @mcp.tool(
        name="get_futures_trades",
        description="Сделки фьючерса FORTS/RFUD через ISS REST.",
    )
    async def get_futures_trades(ticker: str) -> dict[str, Any]:
        return await get_futures_trades_impl(ticker)

    @mcp.tool(
        name="get_futures_securities",
        description="Список фьючерсных контрактов RFUD. Используй, чтобы найти актуальный тикер (Si, RI, BR).",
    )
    async def get_futures_securities() -> dict[str, Any]:
        return await get_futures_securities_impl()

    @mcp.tool(
        name="get_calendar_suspended",
        description="Календарь приостановок торгов по акциям (архив ISS Calendar).",
    )
    async def get_calendar_suspended() -> dict[str, Any]:
        return await get_calendar_suspended_impl()


async def get_share_candles_impl(
    ticker: str,
    interval: int = 24,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Свечи акции TQBR (ISS REST)."""

    try:
        safe = validate_ticker(ticker)
        iv = validate_interval(interval)
        params: dict[str, Any] = {"interval": iv}
        if from_ := validate_optional_date(start):
            params["from"] = from_
        if till := validate_optional_date(end):
            params["till"] = till
        payload = await iss_get(f"{SHARE_BASE}/{safe}/candles.json", get_settings(), params)
        tables = extract_tables(payload)
        return _ok({"ticker": safe, "interval": iv, "tables": tables})
    except Exception as exc:
        return _tool_error(exc)


async def get_share_trades_impl(ticker: str) -> dict[str, Any]:
    """Лента сделок акции TQBR (ISS REST)."""

    try:
        safe = validate_ticker(ticker)
        payload = await iss_get(
            f"{SHARE_BASE}/{safe}/trades.json",
            get_settings(),
            {"limit": MAX_ROWS, "iss.only": "trades"},
        )
        return _ok({"ticker": safe, "tables": extract_tables(payload)})
    except Exception as exc:
        return _tool_error(exc)


async def get_share_marketdata_impl(ticker: str) -> dict[str, Any]:
    """Справочник и marketdata акции TQBR (ISS REST)."""

    try:
        safe = validate_ticker(ticker)
        payload = await iss_get(f"{SHARE_BASE}/{safe}.json", get_settings())
        return _ok({"ticker": safe, "tables": extract_tables(payload)})
    except Exception as exc:
        return _tool_error(exc)


async def get_futures_candles_impl(
    ticker: str,
    interval: int = 24,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Свечи фьючерса RFUD (ISS REST)."""

    try:
        safe = validate_secid(ticker)
        iv = validate_interval(interval)
        params: dict[str, Any] = {"interval": iv}
        if from_ := validate_optional_date(start):
            params["from"] = from_
        if till := validate_optional_date(end):
            params["till"] = till
        payload = await iss_get(f"{FORTS_BASE}/{safe}/candles.json", get_settings(), params)
        return _ok({"ticker": safe, "interval": iv, "tables": extract_tables(payload)})
    except Exception as exc:
        return _tool_error(exc)


async def get_futures_trades_impl(ticker: str) -> dict[str, Any]:
    """Сделки фьючерса RFUD (ISS REST)."""

    try:
        safe = validate_secid(ticker)
        payload = await iss_get(
            f"{FORTS_BASE}/{safe}/trades.json",
            get_settings(),
            {"limit": MAX_ROWS, "iss.only": "trades"},
        )
        return _ok({"ticker": safe, "tables": extract_tables(payload)})
    except Exception as exc:
        return _tool_error(exc)


async def get_futures_securities_impl() -> dict[str, Any]:
    """Список фьючерсов RFUD (ISS REST)."""

    try:
        payload = await iss_get(FORTS_LIST, get_settings())
        tables = extract_tables(payload)
        return _ok({"tables": tables})
    except Exception as exc:
        return _tool_error(exc)


async def get_calendar_suspended_impl() -> dict[str, Any]:
    """Календарь приостановок торгов (архив ISS): фактические и плановые."""

    try:
        latest = await iss_get(CALENDAR_SUSPENDED, get_settings())
        planned = await iss_get(CALENDAR_PLANNED, get_settings())
        return _ok(
            {
                "latest": extract_tables(latest),
                "planned": extract_tables(planned),
            }
        )
    except Exception as exc:
        return _tool_error(exc)
