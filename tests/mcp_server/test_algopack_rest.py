"""Моки ISS REST / ALGOPACK: JSON успех и HTML как отказ в доступе."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_server.tools import (
    get_calendar_suspended_impl,
    get_futures_candles_impl,
    get_share_candles_impl,
    get_share_trades_impl,
    search_ticker_impl,
)
from mcp_server.validation import validate_secid


def _iss_table(columns: list[str], data: list[list[object]]) -> dict:
    return {"columns": columns, "data": data}


@pytest.mark.asyncio
@respx.mock
async def test_share_candles_ok() -> None:
    respx.get(
        url__regex=r"https://iss\.moex\.com/iss/engines/stock/.*/SBER/candles\.json"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            json={"candles": _iss_table(["open", "close"], [[250.0, 251.0]])},
        )
    )
    data = await get_share_candles_impl("sber", interval=24)
    assert data["ok"] is True
    assert data["ticker"] == "SBER"
    assert data["tables"]["candles"][0]["close"] == 251.0


@pytest.mark.asyncio
@respx.mock
async def test_share_trades_ok() -> None:
    respx.get(
        url__regex=r"https://iss\.moex\.com/iss/engines/stock/.*/GAZP/trades\.json"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"trades": _iss_table(["PRICE"], [[100.5]])},
        )
    )
    data = await get_share_trades_impl("GAZP")
    assert data["ok"] is True
    assert data["tables"]["trades"][0]["PRICE"] == 100.5


@pytest.mark.asyncio
@respx.mock
async def test_html_gateway_is_access_denied() -> None:
    respx.get(
        url__regex=r"https://iss\.moex\.com/iss/engines/stock/.*/SBER/candles\.json"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<!DOCTYPE html><html><body>login</body></html>",
        )
    )
    data = await get_share_candles_impl("SBER")
    assert data["ok"] is False
    assert data["error_code"] == "gateway.access-denied"
    assert "Traceback" not in data["error"]


@pytest.mark.asyncio
async def test_invalid_interval_rejected() -> None:
    data = await get_share_candles_impl("SBER", interval=99)
    assert data["ok"] is False
    assert data["error_code"] == "validation_error"


def test_futures_secid_keeps_case() -> None:
    assert validate_secid("SiH6") == "SiH6"


@pytest.mark.asyncio
@respx.mock
async def test_futures_candles_preserves_secid() -> None:
    respx.get(
        url__regex=r"https://iss\.moex\.com/iss/engines/futures/.*/SiH6/candles\.json"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"candles": _iss_table(["close"], [[92.1]])},
        )
    )
    data = await get_futures_candles_impl("SiH6", interval=24)
    assert data["ok"] is True
    assert data["ticker"] == "SiH6"


@pytest.mark.asyncio
@respx.mock
async def test_calendar_suspended_fetches_latest_and_planned() -> None:
    respx.get(
        url__regex=r"https://iss\.moex\.com/iss/archives/files/calendar_stock_session_suspended_latest\.json"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"files": _iss_table(["name"], [["latest.csv"]])},
        )
    )
    respx.get(
        url__regex=r"https://iss\.moex\.com/iss/archives/files/calendars_stock_suspended_planned\.json"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"files": _iss_table(["name"], [["planned.csv"]])},
        )
    )
    data = await get_calendar_suspended_impl()
    assert data["ok"] is True
    assert data["latest"]["files"][0]["name"] == "latest.csv"
    assert data["planned"]["files"][0]["name"] == "planned.csv"


@pytest.mark.asyncio
@respx.mock
async def test_search_rest_prefers_tqbr() -> None:
    respx.get(url__regex=r"https://iss\.moex\.com/iss/securities\.json").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "securities": _iss_table(
                    ["SECID", "SHORTNAME", "PRIMARY_BOARDID"],
                    [["ROSN", "Роснефть", "TQBR"], ["ROSN-12.26", "x", "RFUD"]],
                )
            },
        )
    )
    data = await search_ticker_impl("Роснефть")
    assert data["ok"] is True
    assert data["source"] == "iss_rest"
    assert data["rows"][0]["SECID"] == "ROSN"
    assert len(data["rows"]) == 1
