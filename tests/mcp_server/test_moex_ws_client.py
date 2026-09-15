"""Мок WebSocket STOMP-сервера и тесты MoexWsClient."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import websockets

from mcp_server.config import McpServerSettings
from mcp_server.errors import IssPlusError, IssPlusTimeoutError
from mcp_server.moex_ws_client import MoexWsClient, rows_from_iss_json
from mcp_server.stomp_frames import encode_frame, parse_frames


def _snapshot_body(ticker: str = "SBER") -> str:
    return json.dumps(
        {
            "properties": {"type": "snapshot", "sequence": 1},
            "columns": ["SECID", "LAST"],
            "data": [[ticker, 250.5]],
        }
    )


async def _mock_handler(websocket: Any, path: Any = None) -> None:
    async for raw in websocket:
        for frame in parse_frames(raw):
            cmd = (frame.command or "").upper()
            if cmd == "CONNECT":
                await websocket.send(
                    encode_frame("CONNECTED", {"version": "1.2", "heart-beat": "0,0"})
                )
            elif cmd in ("SUBSCRIBE", "REQUEST"):
                sub_id = frame.headers.get("id", "")
                await websocket.send(
                    encode_frame(
                        "MESSAGE",
                        {"subscription": sub_id, "destination": frame.headers.get("destination", "")},
                        _snapshot_body(),
                    )
                )


async def _error_handler(websocket: Any, path: Any = None) -> None:
    async for raw in websocket:
        for frame in parse_frames(raw):
            if (frame.command or "").upper() == "CONNECT":
                await websocket.send(
                    encode_frame("CONNECTED", {"version": "1.2", "heart-beat": "0,0"})
                )
            elif (frame.command or "").upper() == "SUBSCRIBE":
                await websocket.send(
                    encode_frame(
                        "ERROR",
                        {
                            "message": "gateway.access-denied",
                            "subscription": frame.headers.get("id", ""),
                        },
                        "access denied",
                    )
                )


async def _silent_after_connect(websocket: Any, path: Any = None) -> None:
    async for raw in websocket:
        for frame in parse_frames(raw):
            if (frame.command or "").upper() == "CONNECT":
                await websocket.send(encode_frame("CONNECTED", {"version": "1.2"}))


async def _serve(handler: Any) -> tuple[Any, str]:
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


def _settings(url: str) -> McpServerSettings:
    return McpServerSettings(
        moex_ws_url=url,
        moex_login="test-login",
        moex_passcode="test-passcode-secret",
        mcp_skip_moex_connect=True,
        moex_connect_timeout_sec=3,
        moex_heartbeat_sec=30,
    )


@pytest.mark.asyncio
async def test_rows_from_iss_json() -> None:
    rows = rows_from_iss_json(
        {"columns": ["A", "B"], "data": [[1, 2], [3, 4]], "properties": {"type": "snapshot"}}
    )
    assert rows == [{"A": 1, "B": 2}, {"A": 3, "B": 4}]


@pytest.mark.asyncio
async def test_subscribe_once_with_mock_ws_server() -> None:
    server, url = await _serve(_mock_handler)
    client = MoexWsClient(_settings(url))
    try:
        await client.start()
        await client.wait_connected(timeout=5)
        result = await client.subscribe_once(
            "MXSE.securities",
            'TICKER="MXSE.TQBR.SBER"',
            timeout_sec=5,
        )
        assert result["rows"][0]["SECID"] == "SBER"
        assert result["rows"][0]["LAST"] == 250.5
        assert result["properties"]["type"] == "snapshot"
    finally:
        await client.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_request_command_with_mock_server() -> None:
    server, url = await _serve(_mock_handler)
    client = MoexWsClient(_settings(url))
    try:
        await client.start()
        await client.wait_connected(timeout=5)
        result = await client.subscribe_once(
            "SEARCH.ticker",
            'pattern="SBER"',
            timeout_sec=5,
            command="REQUEST",
            unsubscribe=False,
        )
        assert result["rows"]
    finally:
        await client.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_gateway_access_denied_from_error_frame() -> None:
    server, url = await _serve(_error_handler)
    client = MoexWsClient(_settings(url))
    try:
        await client.start()
        await client.wait_connected(timeout=5)
        with pytest.raises(IssPlusError) as exc:
            await client.subscribe_once("MXSE.securities", 'TICKER="MXSE.TQBR.SBER"', timeout_sec=5)
        assert exc.value.code == "gateway.access-denied"
        assert "стек" not in exc.value.user_message.lower()
    finally:
        await client.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_timeout_when_no_message() -> None:
    server, url = await _serve(_silent_after_connect)
    client = MoexWsClient(_settings(url))
    try:
        await client.start()
        await client.wait_connected(timeout=5)
        with pytest.raises(IssPlusTimeoutError):
            await client.subscribe_once("MXSE.securities", 'TICKER="MXSE.TQBR.SBER"', timeout_sec=1)
    finally:
        await client.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_auth_error_on_connect() -> None:
    async def deny(websocket: Any, path: Any = None) -> None:
        async for raw in websocket:
            for frame in parse_frames(raw):
                if (frame.command or "").upper() == "CONNECT":
                    await websocket.send(
                        encode_frame("ERROR", {"message": "gateway.access-denied"}, "nope")
                    )

    server, url = await _serve(deny)
    client = MoexWsClient(_settings(url))
    try:
        await client.start()
        await asyncio.sleep(0.3)
        assert client.is_connected is False
    finally:
        await client.stop()
        server.close()
        await server.wait_closed()
