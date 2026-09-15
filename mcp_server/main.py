"""Запуск MCP HTTP-сервера (Streamable HTTP) с авторизацией по API-ключу."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn

from mcp_server.config import get_settings
from mcp_server.http_app import AuthHealthASGI
from mcp_server.jsonrpc_mcp import build_jsonrpc_app
from mcp_server.logging_config import setup_logging
from mcp_server.moex_ws_client import MoexWsClient, set_moex_client
from mcp_server.tools import register_tools

log = logging.getLogger("mcp_server")


def _try_register_fastmcp() -> None:
    """Зарегистрировать tools в FastMCP SDK, если пакет доступен.

    HTTP-транспортом остаётся Streamable HTTP JSON-RPC на POST /mcp
    (эквивалент ``json_response=True`` в SDK): так стабильнее между
    версиями ``mcp`` и проще стыковать API-key middleware.
    """

    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        try:
            from mcp.server.mcpserver import MCPServer as FastMCP
        except Exception:
            log.info("mcp SDK FastMCP недоступен — tools обслуживает JSON-RPC слой")
            return
    try:
        try:
            mcp = FastMCP("MOEX1-mcp", stateless_http=True, json_response=True)
        except TypeError:
            mcp = FastMCP("MOEX1-mcp")
        register_tools(mcp)
        log.info("FastMCP tools registered")
    except Exception:
        log.warning("не удалось зарегистрировать FastMCP tools", exc_info=True)


@asynccontextmanager
async def moex_lifespan(_app: Any) -> AsyncIterator[dict[str, Any]]:
    """Поднять WebSocket-клиент ISS+ на время жизни HTTP-сервера."""

    settings = get_settings()
    client = MoexWsClient(settings)
    set_moex_client(client)
    if settings.mcp_skip_moex_connect:
        log.info("moex_connect skipped")
    elif not settings.has_credentials():
        log.warning("moex credentials missing; WS-клиент не запущен")
    else:
        await client.start()
        log.info("moex_ws_client started")
    try:
        yield {"moex_client": client}
    finally:
        await client.stop()
        set_moex_client(None)


def create_app() -> Any:
    """Собрать ASGI-приложение: health + API key + MCP /mcp."""

    settings = get_settings()
    setup_logging(settings)
    _try_register_fastmcp()
    inner = build_jsonrpc_app(lifespan=moex_lifespan)
    return AuthHealthASGI(inner, api_key=settings.mcp_api_key)


def main() -> None:
    """Точка входа: uvicorn на MCP_HOST:MCP_PORT."""

    settings = get_settings()
    setup_logging(settings)
    app = create_app()
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
