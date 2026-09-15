"""Streamable HTTP (JSON-RPC) MCP-сервер.

Режим JSON-ответа: один JSON-RPC на POST /mcp. Используется как основной
HTTP-транспорт; FastMCP из SDK ``mcp`` подключается в ``main.py``, если API
конкретной версии SDK это позволяет.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mcp_server.tools import (
    get_calendar_suspended_impl,
    get_futures_candles_impl,
    get_futures_securities_impl,
    get_futures_trades_impl,
    get_orderbook_impl,
    get_security_snapshot_impl,
    get_share_candles_impl,
    get_share_marketdata_impl,
    get_share_trades_impl,
    search_ticker_impl,
    subscribe_raw_impl,
)

log = logging.getLogger("moex.http")

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "MOEX1-mcp", "version": "0.1.0"}

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_security_snapshot",
        "description": (
            "Текущая цена и параметры акции TQBR. Сначала ISS+ snapshot, "
            "если WebSocket недоступен — ISS REST (поле LAST). "
            "SBER обыкн., SBERP привилегированные."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Код инструмента, только латиница и цифры, например SBER",
                }
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_orderbook",
        "description": (
            "Получить текущий стакан заявок (order book) по тикеру на TQBR. "
            "Используй, когда пользователь просит стакан, заявки, bid/ask."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Код инструмента, например GAZP",
                }
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "search_ticker",
        "description": (
            "Найти тикер по названию. Для цены после поиска вызови "
            "get_share_marketdata или get_security_snapshot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Название или часть названия эмитента/инструмента",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "subscribe_raw",
        "description": (
            "Низкоуровневый диагностический запрос к ISS+: произвольные destination "
            "и selector. Используй только если пользователь явно указывает destination "
            "ISS+ или штатные инструменты не покрывают запрос."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Канал ISS+, например MXSE.securities",
                },
                "selector": {
                    "type": "string",
                    "description": "STOMP selector без переводов строк",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Таймаут ожидания ответа, 1–30 секунд",
                    "default": 5,
                },
            },
            "required": ["destination", "selector"],
        },
    },
    {
        "name": "get_share_candles",
        "description": (
            "Свечи акции TQBR через ISS REST. Для истории цены SBER/GAZP. "
            "interval: 1/10/60 минут или 24 день."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Тикер TQBR, например SBER"},
                "interval": {"type": "integer", "description": "1, 10, 60, 24, 7, 31, 4", "default": 24},
                "start": {"type": "string", "description": "Начало YYYY-MM-DD"},
                "end": {"type": "string", "description": "Конец YYYY-MM-DD"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_share_trades",
        "description": "Лента сделок акции TQBR (ISS REST).",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_share_marketdata",
        "description": (
            "Текущая цена акции TQBR через ISS REST (поле LAST). "
            "Для вопроса «какая цена»: SBER, SBERP, ROSN, GAZP, LKOH."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_futures_candles",
        "description": "Свечи фьючерса FORTS/RFUD через ISS REST.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Код фьючерса, например SiH6"},
                "interval": {"type": "integer", "default": 24},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_futures_trades",
        "description": "Сделки фьючерса FORTS/RFUD через ISS REST.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_futures_securities",
        "description": "Список фьючерсных контрактов RFUD.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_calendar_suspended",
        "description": "Календарь приостановок торгов по акциям (архив ISS).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def _call_snapshot(**kw: Any) -> dict[str, Any]:
    return await get_security_snapshot_impl(str(kw.get("ticker", "")))


async def _call_orderbook(**kw: Any) -> dict[str, Any]:
    return await get_orderbook_impl(str(kw.get("ticker", "")))


async def _call_search(**kw: Any) -> dict[str, Any]:
    return await search_ticker_impl(str(kw.get("query", "")))


async def _call_raw(**kw: Any) -> dict[str, Any]:
    return await subscribe_raw_impl(
        str(kw.get("destination", "")),
        str(kw.get("selector", "")),
        int(kw.get("timeout_sec", 5) or 5),
    )


async def _call_share_candles(**kw: Any) -> dict[str, Any]:
    return await get_share_candles_impl(
        str(kw.get("ticker", "")),
        int(kw.get("interval", 24) or 24),
        kw.get("start"),
        kw.get("end"),
    )


async def _call_share_trades(**kw: Any) -> dict[str, Any]:
    return await get_share_trades_impl(str(kw.get("ticker", "")))


async def _call_share_marketdata(**kw: Any) -> dict[str, Any]:
    return await get_share_marketdata_impl(str(kw.get("ticker", "")))


async def _call_futures_candles(**kw: Any) -> dict[str, Any]:
    return await get_futures_candles_impl(
        str(kw.get("ticker", "")),
        int(kw.get("interval", 24) or 24),
        kw.get("start"),
        kw.get("end"),
    )


async def _call_futures_trades(**kw: Any) -> dict[str, Any]:
    return await get_futures_trades_impl(str(kw.get("ticker", "")))


async def _call_futures_securities(**kw: Any) -> dict[str, Any]:
    return await get_futures_securities_impl()


async def _call_calendar_suspended(**kw: Any) -> dict[str, Any]:
    return await get_calendar_suspended_impl()


HANDLERS: dict[str, ToolHandler] = {
    "get_security_snapshot": _call_snapshot,
    "get_orderbook": _call_orderbook,
    "search_ticker": _call_search,
    "subscribe_raw": _call_raw,
    "get_share_candles": _call_share_candles,
    "get_share_trades": _call_share_trades,
    "get_share_marketdata": _call_share_marketdata,
    "get_futures_candles": _call_futures_candles,
    "get_futures_trades": _call_futures_trades,
    "get_futures_securities": _call_futures_securities,
    "get_calendar_suspended": _call_calendar_suspended,
}


def _jsonrpc_result(rpc_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _jsonrpc_error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


async def handle_rpc(message: dict[str, Any]) -> dict[str, Any] | None:
    """Обработать одно JSON-RPC сообщение. Notifications возвращают None."""

    method = message.get("method")
    rpc_id = message.get("id")
    params = message.get("params") or {}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "initialize":
        return _jsonrpc_result(
            rpc_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )

    if method in ("ping", "notifications/ping"):
        return _jsonrpc_result(rpc_id, {})

    if method == "tools/list":
        return _jsonrpc_result(rpc_id, {"tools": TOOL_SPECS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _jsonrpc_error(rpc_id, -32601, "Unknown tool")
        try:
            data = await handler(**arguments)
        except Exception:
            log.exception("rpc_tool_crash name=%s", name)
            data = {
                "ok": False,
                "error_code": "internal_error",
                "error": "Не удалось получить данные Московской биржи. Попробуйте позже или обратитесь в поддержку.",
            }
        return _jsonrpc_result(
            rpc_id,
            {
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "structuredContent": data,
                "isError": not bool(data.get("ok", True)),
            },
        )

    if rpc_id is None:
        return None
    return _jsonrpc_error(rpc_id, -32601, "Method not found")


async def mcp_endpoint(request: Request) -> Response:
    """POST /mcp — Streamable HTTP JSON; DELETE завершает сессию."""

    if request.method == "DELETE":
        return Response(status_code=204)

    if request.method == "GET":
        return JSONResponse({"error": "SSE not used; POST JSON to /mcp"}, status_code=405)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(_jsonrpc_error(None, -32700, "Parse error"), status_code=400)

    if isinstance(payload, list):
        replies = []
        for item in payload:
            if isinstance(item, dict):
                reply = await handle_rpc(item)
                if reply is not None:
                    replies.append(reply)
        return JSONResponse(replies)

    if not isinstance(payload, dict):
        return JSONResponse(_jsonrpc_error(None, -32600, "Invalid Request"), status_code=400)

    reply = await handle_rpc(payload)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


def build_jsonrpc_app(lifespan: AbstractAsyncContextManager[Any] | None = None) -> Starlette:
    """Собрать Starlette-приложение с endpoint /mcp."""

    routes = [
        Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"]),
        Route("/mcp/", mcp_endpoint, methods=["GET", "POST", "DELETE"]),
    ]
    kwargs: dict[str, Any] = {"routes": routes}
    if lifespan is not None:
        kwargs["lifespan"] = lifespan
    return Starlette(**kwargs)
