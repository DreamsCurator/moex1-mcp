"""HTTP-клиент к MCP-серверу (Streamable HTTP, JSON-ответы)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger("telegram.mcp")


class McpClientError(RuntimeError):
    """Ошибка протокола или HTTP при обращении к MCP."""


class McpHttpClient:
    """Клиент Streamable HTTP: initialize → tools/list / tools/call."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self._timeout = timeout
        self._owns_client = client is None
        self._http = client
        self._session_id: str | None = None
        self._initialized = False

    async def __aenter__(self) -> McpHttpClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-API-Key": self.api_key,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _rpc(self, method: str, params: dict[str, Any] | None = None, *, rpc_id: int = 1) -> Any:
        if self._http is None:
            raise McpClientError("HTTP-клиент не инициализирован")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = await self._http.post(self.endpoint, headers=self._headers(), json=payload)
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        if response.status_code in (401, 403):
            raise McpClientError("MCP server unauthorized")
        if response.status_code >= 400:
            raise McpClientError(f"MCP HTTP {response.status_code}")
        data = _decode_mcp_response(response)
        if isinstance(data, dict) and "error" in data:
            message = data["error"].get("message") if isinstance(data["error"], dict) else "rpc error"
            raise McpClientError(str(message))
        if isinstance(data, dict):
            return data.get("result")
        return data

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._http is None:
            return
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            await self._http.post(self.endpoint, headers=self._headers(), json=payload)
        except httpx.HTTPError:
            log.warning("mcp_notify_failed method=%s", method)

    async def initialize(self) -> None:
        """initialize + notifications/initialized."""

        await self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "moex1-telegram-bot", "version": "0.1.0"},
            },
            rpc_id=1,
        )
        await self._notify("notifications/initialized")
        self._initialized = True
        log.info("mcp_initialized endpoint=%s", self.endpoint)

    async def list_tools(self) -> list[dict[str, Any]]:
        """Список tools MCP-сервера."""

        result = await self._rpc("tools/list", {}, rpc_id=2)
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return []
        log.info("mcp_tools_listed count=%s", len(tools))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Вызвать MCP tool и вернуть разобранный JSON (если получится)."""

        log.info("mcp_tool_call name=%s", name)
        result = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
            rpc_id=3,
        )
        if not isinstance(result, dict):
            return result
        if "structuredContent" in result and result["structuredContent"] is not None:
            return result["structuredContent"]
        content = result.get("content") or []
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
        blob = "\n".join(texts).strip()
        if not blob:
            return result
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return {"text": blob}


def _decode_mcp_response(response: httpx.Response) -> Any:
    content_type = (response.headers.get("content-type") or "").lower()
    text = response.text or ""
    if "text/event-stream" in content_type:
        data_lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return {}
        return json.loads(data_lines[-1])
    if not text:
        return {}
    return json.loads(text)
