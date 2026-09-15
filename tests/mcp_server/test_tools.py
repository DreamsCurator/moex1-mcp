"""Тесты MCP tools с мок-клиентом ISS+."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from mcp_server.errors import IssPlusError, IssPlusTimeoutError, IssPlusValidationError
from mcp_server.moex_ws_client import set_moex_client
from mcp_server import tools as tools_mod
from mcp_server.validation import validate_ticker


class FakeMoex:
    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.result = result or {
            "properties": {"type": "snapshot"},
            "rows": [{"SECID": "SBER", "LAST": 100}],
            "columns": ["SECID", "LAST"],
        }
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    async def subscribe_once(self, destination: str, selector: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((destination, selector, str(kwargs.get("command"))))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def fake_moex() -> FakeMoex:
    client = FakeMoex()
    set_moex_client(client)  # type: ignore[arg-type]
    yield client
    set_moex_client(None)


@pytest.mark.asyncio
async def test_snapshot_ok(fake_moex: FakeMoex) -> None:
    data = await tools_mod.get_security_snapshot_impl("sber")
    assert data["ok"] is True
    assert data["ticker"] == "SBER"
    assert data["rows"][0]["LAST"] == 100
    dest, selector, cmd = fake_moex.calls[0]
    assert dest == "MXSE.securities"
    assert "MXSE.TQBR.SBER" in selector
    assert "LANGUAGE" in selector
    assert cmd == "SUBSCRIBE"


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_ws_timeout_falls_back_to_rest(fake_moex: FakeMoex) -> None:
    fake_moex.error = IssPlusTimeoutError()
    respx.get(url__regex=r"https://iss\.moex\.com/iss/engines/stock/.*/SBER\.json").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"marketdata": {"columns": ["LAST"], "data": [[312.4]]}},
        )
    )
    data = await tools_mod.get_security_snapshot_impl("SBER")
    assert data["ok"] is True
    assert data["tables"]["marketdata"][0]["LAST"] == 312.4


@pytest.mark.asyncio
async def test_orderbook_ok(fake_moex: FakeMoex) -> None:
    data = await tools_mod.get_orderbook_impl("GAZP")
    assert data["ok"] is True
    dest, selector, _ = fake_moex.calls[0]
    assert dest == "MXSE.orderbooks"
    assert selector == 'TICKER="MXSE.TQBR.GAZP"'


@pytest.mark.asyncio
@respx.mock
async def test_search_uses_request(fake_moex: FakeMoex) -> None:
    respx.get(url__regex=r"https://iss\.moex\.com/iss/securities\.json").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"securities": {"columns": ["SECID"], "data": []}},
        )
    )
    data = await tools_mod.search_ticker_impl("Лукойл")
    assert data["ok"] is True
    dest, selector, cmd = fake_moex.calls[0]
    assert dest == "SEARCH.ticker"
    assert selector == 'pattern="Лукойл"'
    assert cmd == "REQUEST"


@pytest.mark.asyncio
async def test_subscribe_raw_ok(fake_moex: FakeMoex) -> None:
    data = await tools_mod.subscribe_raw_impl("MXSE.securities", 'TICKER="MXSE.TQBR.SBER"', 3)
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_invalid_ticker_rejected() -> None:
    with pytest.raises(IssPlusValidationError):
        validate_ticker('SBER" or "1"="1')
    data = await tools_mod.get_security_snapshot_impl('SBER" or "1"="1')
    assert data["ok"] is False
    assert data["error_code"] == "validation_error"


@pytest.mark.asyncio
async def test_invalid_query_quotes() -> None:
    data = await tools_mod.search_ticker_impl('foo" or "1"="1')
    assert data["ok"] is False
    assert data["error_code"] == "validation_error"


@pytest.mark.asyncio
async def test_iss_error_is_user_friendly(fake_moex: FakeMoex) -> None:
    fake_moex.error = IssPlusError("gateway.invalidrequest", "bad")
    data = await tools_mod.get_orderbook_impl("SBER")
    assert data["ok"] is False
    assert data["error_code"] == "gateway.invalidrequest"
    assert "Traceback" not in data["error"]
    assert "gateway.invalidrequest" not in data["error"] or "Не удалось" in data["error"]
