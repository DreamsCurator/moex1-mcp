"""Минимальный encode/decode STOMP-фреймов для ISS+ поверх WebSocket.

Пакет ``stomp.py`` ориентирован на TCP-клиент и плохо стыкуется с WebSocket
ISS+ (свой CONNECT с ``domain/login/passcode``), поэтому парсер реализован
здесь самостоятельно. Формат соответствует STOMP 1.2: команда, заголовки,
пустая строка, тело, завершающий NUL.
"""

from __future__ import annotations

from dataclasses import dataclass, field


NUL = "\x00"


@dataclass
class StompFrame:
    """Один STOMP-фрейм."""

    command: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    def encode(self) -> str:
        """Сериализовать фрейм в текст с завершающим NUL."""

        lines = [self.command]
        for key, value in self.headers.items():
            # Защита от header injection: перевод строки в значениях недопустим.
            safe_key = str(key).replace("\n", "").replace("\r", "").replace(":", "")
            safe_value = str(value).replace("\n", " ").replace("\r", " ")
            lines.append(f"{safe_key}:{safe_value}")
        # Пустая строка (\n\n) отделяет заголовки от тела по STOMP 1.2.
        return "\n".join(lines) + "\n\n" + self.body + NUL


def encode_frame(command: str, headers: dict[str, str] | None = None, body: str = "") -> str:
    """Удобный конструктор закодированного фрейма."""

    return StompFrame(command=command, headers=headers or {}, body=body).encode()


def parse_frames(raw: str | bytes) -> list[StompFrame]:
    """Разобрать один или несколько STOMP-фреймов из WebSocket-сообщения.

    Heartbeat (``\\n`` / пустой NUL-фрейм) пропускается.
    """

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    frames: list[StompFrame] = []
    remaining = raw
    while remaining:
        if remaining[0] in "\n\r":
            remaining = remaining.lstrip("\r\n")
            continue
        if remaining.startswith(NUL):
            remaining = remaining[1:]
            continue
        if NUL not in remaining:
            # Неполный фрейм — пытаемся разобрать как один кадр без NUL
            # (некоторые реализации шлют кадр без терминатора в одном WS-message).
            chunk, remaining = remaining, ""
        else:
            chunk, remaining = remaining.split(NUL, 1)
        chunk = chunk.strip("\r")
        if not chunk.strip():
            continue
        frames.append(_parse_one(chunk))
    return frames


def _parse_one(chunk: str) -> StompFrame:
    header_part, sep, body = chunk.partition("\n\n")
    if not sep:
        header_part, sep, body = chunk.partition("\r\n\r\n")
        if not sep:
            body = ""
    lines = header_part.replace("\r\n", "\n").split("\n")
    command = (lines[0] if lines else "").strip()
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()
    return StompFrame(command=command, headers=headers, body=body)


def connect_frame(login: str, passcode: str, *, domain: str = "passport") -> str:
    """CONNECT для подписчика ALGOPACK (ISS+).

    Формат как в рабочих примерах ISS+: только domain/login/passcode.
    Лишние заголовки (accept-version/heart-beat) на шлюзе infocx дают ERROR.
    """

    return encode_frame(
        "CONNECT",
        {
            "domain": domain,
            "login": login,
            "passcode": passcode,
        },
    )


def subscribe_frame(subscription_id: str, destination: str, selector: str) -> str:
    """SUBSCRIBE на destination ISS+."""

    return encode_frame(
        "SUBSCRIBE",
        {
            "id": subscription_id,
            "destination": destination,
            "selector": selector,
            "ack": "auto",
        },
    )


def unsubscribe_frame(subscription_id: str) -> str:
    """UNSUBSCRIBE по id подписки."""

    return encode_frame("UNSUBSCRIBE", {"id": subscription_id})


def request_frame(request_id: str, destination: str, selector: str) -> str:
    """REQUEST (одноразовый запрос ISS+).

    Точный глагол REQUEST в публичной доке ISS+ описан неоднозначно;
    кадр повторяет структуру SUBSCRIBE (id/destination/selector), как задано
    в спецификации проекта для ``SEARCH.ticker``.
    """

    return encode_frame(
        "REQUEST",
        {
            "id": request_id,
            "destination": destination,
            "selector": selector,
        },
    )


def disconnect_frame() -> str:
    """DISCONNECT перед закрытием сокета."""

    return encode_frame("DISCONNECT", {"receipt": "bye"})
