# MOEX1-mcp

Монорепозиторий из двух сервисов:

1. **MCP-сервер `MOEX1-mcp`** — котировки и стаканы через ISS+ WebSocket (STOMP) и ISS REST (свечи, сделки, календарь).
2. **Telegram-бот** на aiogram 3 — по тексту пользователя OpenAI выбирает MCP tool, бот вызывает сервер по HTTP и отвечает на русском.

Модель LLM задаётся переменной `OPENAI_MODEL` (по умолчанию **`gpt-5.6-luna`**). Другая модель не подставляется автоматически: если API её не знает, это пишется в лог, пользователю уходит общее сообщение об ошибке.

## Архитектура

```
Пользователь → Telegram → aiogram bot → OpenAI (function/tool calling)
                                  │
                                  ├─ если LLM выбрал MCP tool → HTTP-запрос к MCP-серверу
                                  │        MCP-сервер → WebSocket (STOMP) → MOEX ISS+
                                  │                    и/или ISS REST (свечи, сделки, календарь)
                                  │        ← результат ← ответ
                                  │
                                  └─ если подходящего tool нет → сообщение:
                                     "Запрос не может быть обработан в автоматическом режиме,
                                      направьте заявку в поддержку"
                                  │
                                  ▼
                          Финальный ответ пользователю в Telegram
```

MCP-сервер слушает Streamable HTTP (`POST /mcp`, JSON-RPC, режим JSON-ответа). Бот подключается по `MCP_SERVER_URL` и передаёт `MCP_API_KEY` в заголовке `X-API-Key`.

## MCP tools

| Tool | Параметры | Назначение |
|---|---|---|
| `get_security_snapshot` | `ticker: str` | Снепшот бумаги. Destination `MXSE.securities`, selector `TICKER="MXSE.TQBR.{ticker}" and LANGUAGE="ru"` |
| `get_orderbook` | `ticker: str` | Стакан заявок. Destination `MXSE.orderbooks`, selector `TICKER="MXSE.TQBR.{ticker}"`. Подписка → первый snapshot → отписка |
| `search_ticker` | `query: str` | Поиск инструмента. REQUEST на `SEARCH.ticker`, selector `pattern="{query}"` |
| `subscribe_raw` | `destination`, `selector`, `timeout_sec=5` | Диагностическая подписка на произвольный канал ISS+ |
| `get_share_candles` | `ticker`, `interval=24`, `start`, `end` | Свечи акции TQBR (ISS REST). `interval`: 1/10/60 мин или 24/7/31/4 |
| `get_share_trades` | `ticker` | Лента сделок акции TQBR (ISS REST) |
| `get_share_marketdata` | `ticker` | Справочник и marketdata акции TQBR (ISS REST) |
| `get_futures_candles` | `ticker`, `interval=24`, `start`, `end` | Свечи фьючерса RFUD (ISS REST), тикер как `SiH6` |
| `get_futures_trades` | `ticker` | Сделки фьючерса RFUD (ISS REST) |
| `get_futures_securities` | — | Список контрактов RFUD (обрезается до 80 строк) |
| `get_calendar_suspended` | — | Архив приостановок торгов: фактические и плановые |

Тикер принимается только из букв и цифр (защита от STOMP selector injection). Для фьючерсов регистр сохраняется (`SiH6`).

Каталог [ALGOPACK REST](https://moexalgo.github.io/docs/api): Super Candles, FUTOI, HI2, Mega Alerts, ISS Calendar (`/iss/calendars*.json`) и REST-стакан (`orderbook.json`) с `MOEX_ALGOPACK_TOKEN` в этой среде отвечают **HTML**, а не JSON — в MCP их нет.

## Установка и настройка `.env`

Нужен Python 3.11+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env  # Linux/macOS
```

Заполните `.env` (файл в `.gitignore`, в git не попадает):

| Переменная | Откуда взять |
|---|---|
| `MOEX_LOGIN` / `MOEX_PASSCODE` | Логин и пароль подписки ALGOPACK (кабинет MOEX / ALGOPACK) для ISS+ WebSocket |
| `MOEX_ALGOPACK_TOKEN` | REST APIKEY (`Authorization: Bearer`) для ISS. Короткий `login:passcode` тоже принимается |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `OPENAI_API_KEY` | Кабинет OpenAI / ключ API |
| `OPENAI_MODEL` | По умолчанию `gpt-5.6-luna` — не меняйте без запроса заказчика |
| `MCP_API_KEY` | Придумайте длинный случайный секрет (общий для сервера и бота) |
| `MCP_SERVER_URL` | Локально `http://127.0.0.1:8765`, в Docker `http://mcp_server:8765` |

## Запуск

### Локально (два процесса)

```bash
python -m mcp_server
python -m telegram_bot
```

Сервер: `http://127.0.0.1:8765/health` и `POST http://127.0.0.1:8765/mcp`.

### Docker

```bash
docker compose up --build
```

Сеть общая: бот ходит на `http://mcp_server:8765`.

## Примеры запросов в Telegram

| Сообщение пользователя | Ожидаемое поведение |
|---|---|
| «Какая сейчас цена акций Сбербанка?» | LLM вызывает `get_security_snapshot` с тикером `SBER` и отвечает текущими данными |
| «Покажи стакан заявок по тикеру GAZP» | Вызов `get_orderbook` |
| «Найди тикер компании Лукойл» | Вызов `search_ticker` |
| «Свечи SBER за последние дни» | Вызов `get_share_candles` |
| «Какие фьючерсы Si сейчас торгуются?» | `get_futures_securities` и/или `get_futures_candles` |
| «Какая погода в Москве завтра?» | Фиксированный fallback: запрос нельзя обработать автоматически, направьте заявку в поддержку |
| «Покажи мой баланс на бирже» | Тот же fallback (нет подходящего tool) |
| `/start`, `/help` | Приветствие и примеры |

## Логирование

- Каталог: `logs/` (создаётся при старте).
- Файлы с ротацией (5 МБ × 5): `logs/mcp_server.log`, `logs/telegram_bot.log`.
- Формат: JSON-строки (`ts`, `level`, `logger`, `msg`).
- Логгеры: `moex.websocket`, `moex.mcp.tools`, `moex.errors`, `telegram.bot`, `telegram.llm`, `telegram.mcp`, `telegram.errors`.
- Уровень: `LOG_LEVEL` (по умолчанию `INFO`).

## Безопасность

- В логах и ответах пользователю редактируются `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `MCP_API_KEY`, `MOEX_PASSCODE` / `MOEX_ALGOPACK_TOKEN`.
- MCP-сервер отклоняет запросы без верного `X-API-Key` / `Authorization: Bearer` (401, без внутренних деталей). `/health` открыт для docker healthcheck.
- Тикер/query/selector валидируются до сборки STOMP-кадра.
- Результаты tools передаются модели внутри `<exchange_data>` как недоверенные данные, не как инструкции.
- Rate limit: `BOT_RATE_LIMIT_PER_MINUTE` (по умолчанию 10 сообщений/мин на user_id).
- Пользователь не видит стектрейсы, пути и токены — только короткое сообщение об ошибке.

## Тесты

```bash
pytest tests -m "not integration"
```

Интеграционные тесты (`tests/integration/test_integration_live.py`) требуют реальных ключей в `.env` и после их появления запускаются отдельно. Security-набор (`tests/integration/test_security.py`) работает на моках и входит в обычный прогон.

## Известные ограничения

- Зависит от доступности `wss://iss.moex.com/infocx/v3/websocket` и действующей подписки ALGOPACK.
- ISS+ WebSocket принимает passport login/пароль, а не REST APIKEY ALGOPACK. Если шлюз отвечает Access denied, задайте `MOEX_LOGIN` (email passport.moex.com) и `MOEX_PASSCODE`.
- REST-свечи и сделки TQBR/RFUD на `https://iss.moex.com` доступны и без datashop-подписки (публичный ISS). Платные разделы Super Candles / FUTOI / HI2 / Mega Alerts требуют рабочий datashop JSON, иначе шлюз отдаёт HTML.
- Штатные WebSocket tools рассчитаны на акции TQBR (`MXSE.TQBR.{ticker}`). Фьючерсы по REST покрыты отдельными tools; прочие `destination` ISS+ — через `subscribe_raw`.
- Глагол STOMP `REQUEST` для `SEARCH.ticker` взят из спецификации проекта; если ISS+ ожидает иной кадр, это будет видно по `ERROR`/`timeout` в логах.
- Поле `LANGUAGE="ru"` в selector снепшота задано спецификацией; поведение при его отсутствии в доке ISS+ неоднозначно.
- Бот не торгует и не показывает брокерский баланс.
- Модель `gpt-5.6-luna` не заменяется на другую при ошибке API. Для tool calling в Chat Completions ей нужен `reasoning_effort=none`.

## Документация ISS+

Ориентиры: [WebSocket ALGOPACK](https://moexalgo.github.io/docs/websocket/websocket), URL `wss://iss.moex.com/infocx/v3/websocket`, subprotocol `STOMP`, CONNECT с `domain: passport`, `login`, `passcode`.
