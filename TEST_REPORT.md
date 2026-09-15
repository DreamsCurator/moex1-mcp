# TEST_REPORT.md

Дата прогона: 2026-09-14. Секреты из `.env` в отчёт не включены.

## Сводка

| Набор | Результат |
|---|---|
| Unit + security (`pytest tests -m "not integration"`) | **54 passed** |
| Live ISS+ WebSocket + 4 tools (ALGOPACK token) | **FAILED** — `Access denied` |
| Live ISS+ DEMO `guest`/`guest` + 4 tools | **PASSED** |
| Live ISS REST (`test_live_iss_rest_tools`) | **PASSED** (~9 с) |
| Live OpenAI (`gpt-5.6-luna`, tool calling) | **PASSED** |
| Live Telegram `getMe` | **PASSED** |
| Live Telegram sendMessage | **SKIPPED** — нет `TELEGRAM_TEST_CHAT_ID` |
| `docker compose up` | **не выполнен** — Docker CLI есть, демон Desktop не запущен |

## Что настроено в `.env` (только факт наличия)

- `MOEX_ALGOPACK_TOKEN` — задан (длинный opaque-токен, без `MOEX_LOGIN`/`MOEX_PASSCODE`)
- `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-5.6-luna` — заданы
- `TELEGRAM_BOT_TOKEN`, `MCP_API_KEY` — заданы
- `TELEGRAM_TEST_CHAT_ID` — не задан

## ALGOPACK REST (каталог https://moexalgo.github.io/docs/api)

Проверены разделы каталога с `Authorization: Bearer` на `https://iss.moex.com` и `https://apim.moex.com`. Токен в лог не писался.

**JSON 200 — добавлены в MCP:**

| Раздел каталога | Эндпоинт | MCP tool |
|---|---|---|
| Real-time market data — акции | TQBR `.../SBER.json`, `candles.json`, `trades.json` | `get_share_marketdata`, `get_share_candles`, `get_share_trades` |
| Real-time market data — фьючерсы | RFUD `securities.json`, `SiH6/candles.json`, `trades.json` | `get_futures_securities`, `get_futures_candles`, `get_futures_trades` |
| ISS Calendar — архивы | `calendar_stock_session_suspended_latest.json`, `calendars_stock_suspended_planned.json` | `get_calendar_suspended` |

**HTTP 200, но HTML (~14 KB), не JSON — в MCP не добавлялись:**

- Super Candles EQ/FO/FX: tradestats, orderstats, obstats
- FUTOI, HI2, Mega Alerts
- ISS Calendar JSON (`/iss/calendars.json` и stock/futures/currency)
- REST orderbook (`.../orderbook.json` для акций и фьючерсов)

Вывод: токен работает как Bearer для публичного ISS; datashop ALGOPACK (Super Candles и соседние продукты) в этой среде отдаёт HTML-шлюз, а не таблицы. `apim.moex.com` имеет самоподписанную цепочку TLS; рабочие REST-вызовы идут на `https://iss.moex.com`.

WebSocket ISS+ с тем же токеном по-прежнему `Access denied` (нужен passport login/пароль).

## Live ISS+

Подключение к `wss://iss.moex.com/infocx/v3/websocket` **устанавливается**. STOMP `CONNECT` с токеном как passcode получает ERROR:

- `message`: Access denied
- классификация: `gateway.access-denied`

Реконнект при отказе в доступе остановлен (не долбим шлюз).

**Причина:** ISS+ infocx ждёт **passport email + пароль** (`domain: passport`, `login`, `passcode`), как в рабочих примерах ALGOPACK. Длинный `MOEX_ALGOPACK_TOKEN` — типичный REST APIKEY (`Authorization: Bearer` для ISS), WebSocket его не принимает.

**Что исправлено по ходу:**

- opaque/JWT токен больше не режется по `:`;
- CONNECT упрощён до `domain/login/passcode` (лишние STOMP-заголовки давали общий `gateway.exception`);
- `Access denied` мапится на `gateway.access-denied`;
- пользователю/LLM уходит дружелюбное сообщение, без стектрейса.

## Live ISS+ (повтор 2026-09-14, DEMO)

Повтор с публичными демо-кредами ISS+ (`domain=DEMO`, `login=guest`, `passcode=guest`) — **PASSED** за ~17 с:

- CONNECT → CONNECTED
- `get_security_snapshot(SBER)` — ok
- `get_orderbook(GAZP)` — ok
- `search_ticker(Сбербанк)` — ok
- `subscribe_raw(MXSE.securities, TICKER=MXSE.TQBR.SBER)` — ok
- невалидный тикер и injection — без стектрейса

Для постоянного демо-режима в `.env`:

```
MOEX_DOMAIN=DEMO
MOEX_LOGIN=guest
MOEX_PASSCODE=guest
```

Пара login/passcode имеет приоритет над `MOEX_ALGOPACK_TOKEN`. Passport-подписка по-прежнему нужна для боевого ALGOPACK, не для этого демо-контура.

```
MOEX_LOGIN=<email от passport.moex.com>
MOEX_PASSCODE=<пароль passport>
```

После этого можно повторно запустить `pytest tests/integration/test_integration_live.py::test_live_iss_connect_and_tools`.

## Live OpenAI

Модель **`gpt-5.6-luna` не подменялась**.

Первый вызов с tools вернул 400: function tools не поддерживаются при ненулевом `reasoning_effort` в `/v1/chat/completions`. Исправление: `reasoning_effort=none` (это параметр запроса, не смена модели). Повторный прогон:

| Запрос | Результат |
|---|---|
| «Какая погода в Москве завтра?» | fallback в поддержку |
| «Покажи мой баланс на бирже» | fallback |
| prompt-injection (system prompt / токены) | canary и ключ не утекли |
| «Какая сейчас цена акций Сбербанка?» | выбран MCP tool (`get_security_snapshot` или `search_ticker`) |

## Live Telegram

- `getMe` успешен: токен валиден, бот доступен.
- Сквозной «сообщение в Telegram → ответ» не гонялся: нет `TELEGRAM_TEST_CHAT_ID`. После ISS+ passport-кредов можно задать id чата и повторить.

## Security-тесты

| Проверка | Статус |
|---|---|
| MCP без ключа / с неверным ключом → 401, без утечки деталей | passed (предыдущий прогон) |
| Верный `MCP_API_KEY` → initialize + `tools/list` | список включает ISS+ и REST tools |
| Injection в `ticker`/`query`/selector отсекается валидацией | passed |
| Секреты не попадают в отформатированные логи | passed |
| Rate-limit | passed |
| Prompt-injection не раскрывает system prompt и ключи | passed |
| ISS+ `gateway.*` → дружелюбное сообщение, не стектрейс | passed |

## Что чинили после вставки ключей

1. Парсер `MOEX_ALGOPACK_TOKEN`: длинный ключ как passcode целиком.
2. CONNECT к ISS+ как в рабочих примерах (без лишних заголовков).
3. Классификация `Access denied`.
4. OpenAI `gpt-5.6-luna`: `reasoning_effort=none` для tool calling.
5. Security-тесты HTTP: ключ берётся из настроек, а не захардкоженный test-ключ (иначе 401 против реального `.env`).
6. ISS REST tools по каталогу ALGOPACK: только эндпоинты, которые реально отдают JSON.

## Docker

`docker compose.yml` готов (`mcp_server` + `telegram_bot`). `docker compose up --build -d` не стартовал: демон Docker Desktop не запущен (`npipe: dockerDesktopLinuxEngine`). После запуска Desktop:

```bash
docker compose up --build
```

Healthcheck MCP: `GET http://127.0.0.1:8765/health` (без API-ключа).
