"""ASGI-обёртка: health-check и проверка MCP_API_KEY."""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("moex.http")

Send = Callable[[dict[str, Any]], Awaitable[None]]
Receive = Callable[[], Awaitable[dict[str, Any]]]


async def _send_json(send: Send, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _header_map(scope: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        result[key.decode("latin1").lower()] = value.decode("latin1")
    return result


def extract_api_key(headers: dict[str, str]) -> str:
    """Ключ из X-API-Key или Authorization: Bearer."""

    key = (headers.get("x-api-key") or "").strip()
    if key:
        return key
    auth = (headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


class AuthHealthASGI:
    """Пропускает lifespan, отдаёт /health без ключа, остальное — только с API key."""

    def __init__(self, app: Any, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        method = (scope.get("method") or "GET").upper()
        if path.rstrip("/") in ("/health", "/healthz") and method == "GET":
            await _send_json(send, 200, {"status": "ok"})
            return

        headers = _header_map(scope)
        provided = extract_api_key(headers)
        if not self.api_key or not hmac.compare_digest(provided, self.api_key):
            log.warning("unauthorized path=%s", path)
            await _send_json(send, 401, {"error": "Unauthorized"})
            return

        await self.app(scope, receive, send)
