"""HTTP-клиент к ISS REST / ALGOPACK (Bearer из MOEX_ALGOPACK_TOKEN).

Документация: https://moexalgo.github.io/docs/api/
База ``https://iss.moex.com`` — валидный TLS. Шлюз ``apim.moex.com`` в этой среде
отдаёт самоподписанную цепочку, поэтому realtime ISS вызываем на iss.moex.com.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mcp_server.config import McpServerSettings
from mcp_server.errors import IssPlusError
from mcp_server.moex_ws_client import rows_from_iss_json

log = logging.getLogger("moex.mcp.tools")

ISS_REST_BASE = "https://iss.moex.com"
MAX_ROWS = 80

SHARE_BASE = "/iss/engines/stock/markets/shares/boards/tqbr/securities"
FORTS_BASE = "/iss/engines/futures/markets/forts/boards/rfud/securities"
FORTS_LIST = "/iss/engines/futures/markets/forts/boards/rfud/securities.json"
SECURITIES_SEARCH = "/iss/securities.json"
CALENDAR_SUSPENDED = "/iss/archives/files/calendar_stock_session_suspended_latest.json"
CALENDAR_PLANNED = "/iss/archives/files/calendars_stock_suspended_planned.json"


def _headers(settings: McpServerSettings) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = (settings.moex_algopack_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def extract_tables(payload: dict[str, Any], *, limit: int = MAX_ROWS) -> dict[str, list[dict[str, Any]]]:
    """Достать все ISS-блоки columns/data, обрезать длинные таблицы."""

    tables: dict[str, list[dict[str, Any]]] = {}
    for name, block in payload.items():
        if not isinstance(block, dict):
            continue
        if "columns" in block and "data" in block:
            rows = rows_from_iss_json(block)
            tables[str(name)] = rows[:limit]
    return tables


async def iss_get(path: str, settings: McpServerSettings, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET JSON с ISS REST. HTML-ответ (не JSON) считаем ошибкой шлюза."""

    url = ISS_REST_BASE + path
    query: dict[str, Any] = {"iss.meta": "off"}
    if params:
        query.update(params)
    log.info("iss_rest_get path=%s", path)
    last_error: httpx.HTTPError | None = None
    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=25.0,
                follow_redirects=True,
                headers=_headers(settings),
            ) as client:
                response = await client.get(url, params=query)
            break
        except httpx.HTTPError as exc:
            last_error = exc
            log.warning("iss_rest_transport attempt=%s error=%s", attempt + 1, type(exc).__name__)
    if response is None:
        raise IssPlusError("gateway.transport", "ISS REST transport error") from last_error
    ctype = (response.headers.get("content-type") or "").lower()
    text = response.text or ""
    if response.status_code >= 400:
        raise IssPlusError("gateway.invalidrequest", f"ISS REST HTTP {response.status_code}")
    if "html" in ctype or text.lstrip()[:9].lower().startswith("<!doctype") or text.lstrip()[:6].lower() == "<html":
        raise IssPlusError(
            "gateway.access-denied",
            "ISS REST вернул HTML вместо JSON (нет доступа к ALGOPACK datashop)",
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise IssPlusError("gateway.invalidrequest", "ISS REST вернул не JSON") from exc
    if not isinstance(payload, dict):
        raise IssPlusError("gateway.invalidrequest", "Некорректный JSON ISS REST")
    return payload
