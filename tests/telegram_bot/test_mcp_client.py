"""Тесты HTTP MCP-клиента с моком HTTP (respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from telegram_bot.mcp_client import McpClientError, McpHttpClient

ENDPOINT = "http://mcp.test/mcp"


def _jsonrpc_result(rpc_id: int, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


@pytest.mark.asyncio
@respx.mock
async def test_list_and_call_tools() -> None:
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_jsonrpc_result(
                    1,
                    {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "serverInfo": {"name": "MOEX1-mcp", "version": "0.1.0"},
                    },
                ),
                headers={"mcp-session-id": "sess-1"},
            ),
            httpx.Response(202),
            httpx.Response(
                200,
                json=_jsonrpc_result(
                    2,
                    {
                        "tools": [
                            {
                                "name": "get_security_snapshot",
                                "description": "snapshot",
                                "inputSchema": {"type": "object", "properties": {"ticker": {"type": "string"}}},
                            }
                        ]
                    },
                ),
            ),
            httpx.Response(
                200,
                json=_jsonrpc_result(
                    3,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": '{"ok": true, "ticker": "SBER"}',
                            }
                        ]
                    },
                ),
            ),
        ]
    )
    async with McpHttpClient(ENDPOINT, "test-mcp-key") as client:
        tools = await client.list_tools()
        assert tools[0]["name"] == "get_security_snapshot"
        data = await client.call_tool("get_security_snapshot", {"ticker": "SBER"})
        assert data["ok"] is True
        assert data["ticker"] == "SBER"
    assert respx.calls[0].request.headers["x-api-key"] == "test-mcp-key"


@pytest.mark.asyncio
@respx.mock
async def test_unauthorized() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(401, json={"error": "Unauthorized"}))
    client = McpHttpClient(ENDPOINT, "wrong")
    client._http = httpx.AsyncClient()
    try:
        with pytest.raises(McpClientError, match="unauthorized"):
            await client.initialize()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_sse_response_parsed() -> None:
    payload = _jsonrpc_result(1, {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {}})
    body = "event: message\ndata: " + __import__("json").dumps(payload) + "\n\n"
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
            httpx.Response(202),
        ]
    )
    async with McpHttpClient(ENDPOINT, "k") as client:
        assert client._initialized is True
