"""Probe ALGOPACK REST endpoints. Prints status codes only, never the token."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _last_weekday() -> str:
    day = date.today()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def main() -> None:
    token = (os.getenv("MOEX_ALGOPACK_TOKEN") or "").strip()
    if not token:
        raise SystemExit("MOEX_ALGOPACK_TOKEN is not set")

    day = _last_weekday()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    hosts = ["https://iss.moex.com", "https://apim.moex.com"]
    paths = [
        # Super Candles EQ
        f"/iss/datashop/algopack/eq/tradestats.json?date={day}",
        f"/iss/datashop/algopack/eq/tradestats/SBER.json?date={day}",
        f"/iss/datashop/algopack/eq/orderstats.json?date={day}",
        f"/iss/datashop/algopack/eq/orderstats/SBER.json?date={day}",
        f"/iss/datashop/algopack/eq/obstats.json?date={day}",
        f"/iss/datashop/algopack/eq/obstats/SBER.json?date={day}",
        # Super Candles FO / FX
        f"/iss/datashop/algopack/fo/tradestats.json?date={day}",
        f"/iss/datashop/algopack/fo/orderstats.json?date={day}",
        f"/iss/datashop/algopack/fo/obstats.json?date={day}",
        f"/iss/datashop/algopack/fx/tradestats.json?date={day}",
        f"/iss/datashop/algopack/fx/orderstats.json?date={day}",
        f"/iss/datashop/algopack/fx/obstats.json?date={day}",
        # FUTOI / HI2 / alerts
        f"/iss/datashop/algopack/fo/futoi.json?date={day}",
        f"/iss/datashop/algopack/eq/hi2.json?date={day}",
        f"/iss/datashop/algopack/eq/hi2/SBER.json?date={day}",
        f"/iss/datashop/algopack/fo/hi2.json?date={day}",
        f"/iss/datashop/algopack/fx/hi2.json?date={day}",
        f"/iss/datashop/algopack/eq/alerts.json?date={day}",
        f"/iss/datashop/algopack/eq/megaalerts.json?date={day}",
        "/iss/datashop/algopack/eq/alerts.json",
        # Real-time shares
        "/iss/engines/stock/markets/shares/boards/tqbr/securities/SBER.json",
        "/iss/engines/stock/markets/shares/boards/tqbr/securities/SBER/candles.json?interval=24",
        "/iss/engines/stock/markets/shares/boards/tqbr/securities/SBER/orderbook.json",
        "/iss/engines/stock/markets/shares/boards/tqbr/securities/SBER/trades.json",
        # Real-time futures (SiH6-like; Si is currency futures prefix)
        "/iss/engines/futures/markets/forts/boards/rfud/securities.json",
        "/iss/engines/futures/markets/forts/boards/rfud/securities/SiH6/candles.json?interval=24",
        "/iss/engines/futures/markets/forts/boards/rfud/securities/SiH6/orderbook.json",
        "/iss/engines/futures/markets/forts/boards/rfud/securities/SiH6/trades.json",
        # Calendar
        "/iss/calendars.json",
        "/iss/calendars/stock.json",
        "/iss/calendars/stock/static.json",
        "/iss/calendars/stock/session.json",
        "/iss/calendars/stock/session/settlecodes.json",
        "/iss/calendars/futures/session.json",
        "/iss/calendars/currency/securities.json",
        "/iss/archives/files/calendar_stock_session_suspended_latest.json",
        "/iss/archives/files/calendars_stock_suspended_planned.json",
    ]

    rows = []
    for base in hosts:
        verify = base.endswith("iss.moex.com")
        print("HOST", base, "verify", verify)
        with httpx.Client(headers=headers, timeout=25.0, follow_redirects=True, verify=verify) as client:
            for path in paths:
                url = base + path
                short = f"{base.split('//',1)[1]}{path.split('?')[0]}"
                try:
                    resp = client.get(url)
                    status = resp.status_code
                    ctype = (resp.headers.get("content-type") or "")[:40]
                    nkeys = 0
                    nrows = 0
                    if "json" in ctype.lower() or (resp.text[:1] in "{["):
                        try:
                            payload = resp.json()
                            if isinstance(payload, dict):
                                nkeys = len(payload)
                                for block in payload.values():
                                    if isinstance(block, dict) and isinstance(block.get("data"), list):
                                        nrows += len(block["data"])
                        except Exception:
                            pass
                    rows.append(
                        {
                            "host": base,
                            "path": path.split("?")[0],
                            "status": status,
                            "ok": 200 <= status < 300,
                            "content_type": ctype,
                            "json_blocks": nkeys,
                            "rows": nrows,
                        }
                    )
                    print(f"{status} rows={nrows} {short}")
                except httpx.HTTPError as exc:
                    rows.append(
                        {
                            "host": base,
                            "path": path.split("?")[0],
                            "status": 0,
                            "ok": False,
                            "error": type(exc).__name__,
                        }
                    )
                    print(f"ERR {type(exc).__name__} {short}")

    out = Path("logs") / "algopack_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"date": day, "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
