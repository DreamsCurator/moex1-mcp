"""Конфигурация MCP-сервера из переменных окружения."""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpServerSettings(BaseSettings):
    """Настройки ISS+ и HTTP-транспорта MCP-сервера."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    moex_ws_url: str = "wss://iss.moex.com/infocx/v3/websocket"
    moex_domain: str = "passport"
    moex_login: str = ""
    moex_passcode: str = ""
    moex_algopack_token: str = ""

    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8765
    mcp_api_key: str = Field(default="")
    mcp_skip_moex_connect: bool = False

    log_level: str = "INFO"
    log_dir: Path = Path("logs")

    moex_connect_timeout_sec: float = 15.0
    moex_heartbeat_sec: float = 10.0
    moex_reconnect_max_delay_sec: float = 60.0
    tool_timeout_sec: float = 8.0

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    def uses_opaque_algopack_token(self) -> bool:
        """True, если задан длинный API-токен, а не пара login:passcode."""

        token = (self.moex_algopack_token or "").strip()
        if not token:
            return False
        if len(token) > 80:
            return True
        parts = token.split(".")
        return len(parts) == 3 and parts[0].startswith("eyJ")

    def resolve_credentials(self) -> tuple[str, str]:
        """Вернуть пару (login, passcode) из LOGIN/PASSCODE или из единого токена.

        Поддерживаются:
        - ``MOEX_LOGIN`` + ``MOEX_PASSCODE``;
        - короткий ``MOEX_ALGOPACK_TOKEN`` вида ``login:passcode``;
        - длинный opaque/JWT токен подписки — целиком как passcode
          (не режем по ``:``, чтобы не испортить ключ).

        Значения не логируются вызывающим кодом.
        """

        login = (self.moex_login or "").strip()
        passcode = (self.moex_passcode or "").strip()
        if login and passcode:
            return login, passcode

        token = (self.moex_algopack_token or "").strip()
        if not token:
            raise ValueError(
                "Задайте MOEX_LOGIN и MOEX_PASSCODE либо MOEX_ALGOPACK_TOKEN"
            )

        if self.uses_opaque_algopack_token():
            jwt_login = _login_from_jwt(token)
            return (login or jwt_login or "token"), token

        if ":" in token:
            token_login, token_pass = token.split(":", 1)
            if token_login.strip() and token_pass.strip():
                return token_login.strip(), token_pass.strip()
        if login:
            return login, token
        raise ValueError(
            "MOEX_ALGOPACK_TOKEN должен быть в формате login:passcode, "
            "длинным API-токеном либо задан вместе с MOEX_LOGIN"
        )

    def websocket_headers(self) -> dict[str, str]:
        """Доп. заголовки handshake: Bearer только если нет пары login/passcode."""

        login = (self.moex_login or "").strip()
        passcode = (self.moex_passcode or "").strip()
        if login and passcode:
            return {}
        if self.uses_opaque_algopack_token():
            return {"Authorization": f"Bearer {(self.moex_algopack_token or '').strip()}"}
        return {}

    def has_credentials(self) -> bool:
        """Проверить, что креды заданы, не раскрывая их значения."""

        try:
            login, passcode = self.resolve_credentials()
        except ValueError:
            return False
        return bool(login and passcode)


def _login_from_jwt(token: str) -> str:
    """Достать login из JWT payload без проверки подписи. Ничего не логирует."""

    parts = token.split(".")
    if len(parts) != 3:
        return ""
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("email", "preferred_username", "upn", "unique_name", "sub"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@lru_cache(maxsize=1)
def get_settings() -> McpServerSettings:
    """Вернуть закэшированные настройки сервера."""

    return McpServerSettings()
