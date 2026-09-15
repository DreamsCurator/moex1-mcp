"""Асинхронный STOMP/WebSocket-клиент к ISS+ (ALGOPACK)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

import websockets

from mcp_server.config import McpServerSettings
from mcp_server.errors import IssPlusError, IssPlusTimeoutError, classify_iss_error
from mcp_server.stomp_frames import (
    StompFrame,
    connect_frame,
    disconnect_frame,
    parse_frames,
    request_frame,
    subscribe_frame,
    unsubscribe_frame,
)

log = logging.getLogger("moex.websocket")
error_log = logging.getLogger("moex.errors")

ConnectFactory = Callable[..., Any]


def rows_from_iss_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Превратить ``columns`` + ``data`` ISS+ в список словарей."""

    columns = payload.get("columns") or []
    data = payload.get("data") or []
    rows: list[dict[str, Any]] = []
    if not isinstance(columns, list) or not isinstance(data, list):
        return rows
    for row in data:
        if not isinstance(row, list):
            continue
        item: dict[str, Any] = {}
        for idx, column in enumerate(columns):
            if idx < len(row):
                item[str(column)] = row[idx]
        rows.append(item)
    return rows


class MoexWsClient:
    """Поддерживает одно устойчивое STOMP-соединение с реконнектом."""

    def __init__(
        self,
        settings: McpServerSettings,
        *,
        connect_factory: ConnectFactory | None = None,
    ) -> None:
        self.settings = settings
        self._connect_factory = connect_factory or websockets.connect
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._ws: Any = None
        self._runner: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_interval = settings.moex_heartbeat_sec
        self._auth_attempt = 0
        self.last_error: IssPlusError | None = None
        self._auth_failed = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def start(self) -> None:
        """Запустить фоновый цикл соединения."""

        self._stop.clear()
        self._auth_failed.clear()
        self.last_error = None
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run_loop(), name="moex-ws-loop")

    async def stop(self) -> None:
        """Остановить клиент и закрыть сокет."""

        self._stop.set()
        await self._fail_pending(IssPlusError("gateway.transport", "Соединение закрыто"))
        if self._ws is not None:
            try:
                await self._ws.send(disconnect_frame())
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._runner:
            self._runner.cancel()
            try:
                await self._runner
            except (asyncio.CancelledError, Exception):
                pass
        self._connected.clear()

    async def wait_connected(self, timeout: float | None = None) -> None:
        """Дождаться CONNECTED либо фатальной ошибки аутентификации."""

        timeout_sec = timeout or self.settings.moex_connect_timeout_sec
        connected_task = asyncio.create_task(self._connected.wait())
        auth_task = asyncio.create_task(self._auth_failed.wait())
        done, pending = await asyncio.wait(
            {connected_task, auth_task},
            timeout=timeout_sec,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if self._connected.is_set():
            return
        if self._auth_failed.is_set() and self.last_error is not None:
            raise self.last_error
        raise TimeoutError

    async def subscribe_once(
        self,
        destination: str,
        selector: str,
        *,
        timeout_sec: float = 5,
        unsubscribe: bool = True,
        command: str = "SUBSCRIBE",
        snapshot_only: bool = True,
    ) -> dict[str, Any]:
        """Подписаться (или REQUEST), дождаться ответа, опционально отписаться."""

        if not self.is_connected:
            try:
                await self.wait_connected(timeout=min(timeout_sec, 10))
            except TimeoutError as exc:
                raise IssPlusError(
                    "gateway.not-connected",
                    "Нет активного соединения с ISS+",
                ) from exc

        sub_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[sub_id] = future

        if command.upper() == "REQUEST":
            frame = request_frame(sub_id, destination, selector)
        else:
            frame = subscribe_frame(sub_id, destination, selector)

        started = time.perf_counter()
        log.info(
            "iss_request start command=%s destination=%s timeout_sec=%s id=%s",
            command,
            destination,
            timeout_sec,
            sub_id,
        )
        try:
            await self._send(frame)
            result = await asyncio.wait_for(future, timeout=timeout_sec)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log.info(
                "iss_request done command=%s destination=%s elapsed_ms=%s id=%s",
                command,
                destination,
                elapsed_ms,
                sub_id,
            )
            return result
        except TimeoutError as exc:
            error_log.warning(
                "iss_timeout destination=%s timeout_sec=%s id=%s",
                destination,
                timeout_sec,
                sub_id,
            )
            raise IssPlusTimeoutError(
                f"Таймаут ожидания ответа ISS+ ({timeout_sec} с)"
            ) from exc
        finally:
            self._pending.pop(sub_id, None)
            if unsubscribe and command.upper() != "REQUEST":
                try:
                    await self._send(unsubscribe_frame(sub_id))
                except Exception:
                    log.debug("unsubscribe failed id=%s", sub_id)

    async def _run_loop(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected.clear()
                error_log.error("ws_session_error err=%s", type(exc).__name__)
                if isinstance(exc, IssPlusError):
                    self.last_error = exc
                    if exc.code == "gateway.access-denied":
                        self._auth_failed.set()
                        await self._fail_pending(exc)
                        return
                await self._fail_pending(
                    IssPlusError("gateway.transport", "Разрыв соединения с ISS+")
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(delay * 2, self.settings.moex_reconnect_max_delay_sec)
                log.info("ws_reconnect backoff_sec=%s", delay)

    async def _session(self) -> None:
        login, passcode = self.settings.resolve_credentials()
        use_stomp = self._auth_attempt % 2 == 0
        domain = (self.settings.moex_domain or "passport").strip() or "passport"
        log.info(
            "ws_connecting url=%s auth_mode=%s stomp_subprotocol=%s domain=%s jwt_login=%s",
            self.settings.moex_ws_url,
            "login_passcode" if (self.settings.moex_login and self.settings.moex_passcode) else (
                "opaque_token" if self.settings.uses_opaque_algopack_token() else "login_passcode"
            ),
            use_stomp,
            domain,
            bool(login and login not in {"token", "apikey", "guest"}),
        )
        connect_kwargs: dict[str, Any] = {"ping_interval": None}
        # Bearer на handshake только если нет пары login/passcode (иначе DEMO/passport мешается с APIKEY).
        if self.settings.uses_opaque_algopack_token() and not (
            (self.settings.moex_login or "").strip() and (self.settings.moex_passcode or "").strip()
        ):
            headers = self.settings.websocket_headers()
            if headers:
                connect_kwargs["additional_headers"] = headers
        if use_stomp:
            connect_kwargs["subprotocols"] = ["STOMP"]
        try:
            ws_cm = self._connect_factory(self.settings.moex_ws_url, **connect_kwargs)
        except TypeError:
            connect_kwargs.pop("additional_headers", None)
            try:
                ws_cm = self._connect_factory(self.settings.moex_ws_url, **connect_kwargs)
            except TypeError:
                ws_cm = self._connect_factory(self.settings.moex_ws_url)
        async with ws_cm as ws:
            self._ws = ws
            await ws.send(connect_frame(login, passcode, domain=domain))
            raw = await asyncio.wait_for(ws.recv(), timeout=self.settings.moex_connect_timeout_sec)
            frames = parse_frames(raw)
            if not frames or frames[0].command != "CONNECTED":
                if frames and frames[0].command == "ERROR":
                    err = self._error_from_frame(frames[0])
                    error_log.error(
                        "iss_auth_error code=%s stomp_message=%s header_keys=%s",
                        err.code,
                        (frames[0].headers.get("message") or "")[:120],
                        sorted(frames[0].headers.keys()),
                    )
                    self._auth_attempt += 1
                    raise err
                raise IssPlusError("gateway.exception", "ISS+ не вернул CONNECTED")
            self._apply_heartbeat(frames[0])
            self._connected.set()
            log.info("ws_connected")
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            try:
                await self._receive_loop(ws)
            finally:
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                self._connected.clear()
                self._ws = None

    async def _receive_loop(self, ws: Any) -> None:
        async for raw in ws:
            if self._stop.is_set():
                break
            if raw in ("\n", "\r\n", ""):
                continue
            for frame in parse_frames(raw):
                await self._dispatch(frame)

    async def _dispatch(self, frame: StompFrame) -> None:
        command = (frame.command or "").upper()
        if command == "ERROR":
            err = self._error_from_frame(frame)
            error_log.error("iss_error code=%s", err.code)
            sub_id = frame.headers.get("subscription") or frame.headers.get("receipt-id") or frame.headers.get("id")
            if sub_id and sub_id in self._pending:
                self._resolve(sub_id, exception=err)
            else:
                await self._fail_pending(err)
            return
        if command == "MESSAGE":
            sub_id = (
                frame.headers.get("subscription")
                or frame.headers.get("id")
                or frame.headers.get("correlation-id")
            )
            payload = self._parse_body(frame.body)
            result = {
                "properties": payload.get("properties") if isinstance(payload, dict) else {},
                "rows": rows_from_iss_json(payload) if isinstance(payload, dict) else [],
                "columns": payload.get("columns") if isinstance(payload, dict) else [],
            }
            if sub_id:
                self._resolve(sub_id, value=result)
            return

    def _parse_body(self, body: str) -> dict[str, Any]:
        text = (body or "").strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("iss_non_json_body")
            return {"raw": text[:500]}
        return data if isinstance(data, dict) else {"value": data}

    def _error_from_frame(self, frame: StompFrame) -> IssPlusError:
        header_msg = (
            frame.headers.get("message")
            or frame.headers.get("code")
            or ""
        )
        return classify_iss_error(header_msg, frame.body or "", extra_headers=frame.headers)

    def _apply_heartbeat(self, frame: StompFrame) -> None:
        raw = frame.headers.get("heart-beat") or "10000,10000"
        try:
            sx, _sy = raw.split(",", 1)
            send_ms = int(sx)
        except (TypeError, ValueError):
            send_ms = 10000
        if send_ms > 0:
            self._heartbeat_interval = max(send_ms / 1000.0, 1.0)
        else:
            self._heartbeat_interval = self.settings.moex_heartbeat_sec

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stop.is_set() and self._ws is not None:
                await asyncio.sleep(self._heartbeat_interval)
                try:
                    await self._send("\n")
                except Exception:
                    return
        except asyncio.CancelledError:
            return

    async def _send(self, data: str) -> None:
        async with self._send_lock:
            if self._ws is None:
                raise IssPlusError("gateway.not-connected", "Нет активного соединения с ISS+")
            await self._ws.send(data)

    def _resolve(
        self,
        sub_id: str,
        *,
        value: dict[str, Any] | None = None,
        exception: BaseException | None = None,
    ) -> None:
        future = self._pending.get(sub_id)
        if future is None or future.done():
            return
        if exception is not None:
            future.set_exception(exception)
        elif value is not None:
            future.set_result(value)

    async def _fail_pending(self, exc: BaseException) -> None:
        for sub_id, future in list(self._pending.items()):
            if not future.done():
                future.set_exception(exc)
            self._pending.pop(sub_id, None)


_CLIENT: MoexWsClient | None = None


def set_moex_client(client: MoexWsClient | None) -> None:
    """Подставить клиент (продакшен-старт или мок в тестах)."""

    global _CLIENT
    _CLIENT = client


def get_moex_client() -> MoexWsClient:
    """Вернуть текущий клиент ISS+."""

    if _CLIENT is None:
        raise RuntimeError("MOEX WebSocket-клиент не инициализирован")
    return _CLIENT
