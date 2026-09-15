"""Коды ошибок ISS+ gateway.* и пользовательские исключения."""

from __future__ import annotations

from dataclasses import dataclass

GATEWAY_ERRORS: dict[str, str] = {
    "gateway.exception": "Непредвиденная ошибка на сервере доступа",
    "gateway.timeout": "Таймаут операции",
    "gateway.transport": "Ошибка связи между сервером доступа и RabbitMQ",
    "gateway.access-denied": "Отказ в доступе при аутентификации или запросе ресурса",
    "gateway.invalidrequest": "Неверный запрос ресурса (несуществующий/неавторизованный ресурс, неверные параметры)",
    "gateway.notimplemented": "Функциональность не реализована",
    "gateway.not-connected": "Попытка доступа к ресурсу без аутентификации",
}

USER_FRIENDLY_ISS_ERROR = (
    "Не удалось получить данные Московской биржи. Попробуйте позже или обратитесь в поддержку."
)


@dataclass
class IssPlusError(Exception):
    """Ошибка ISS+ с кодом gateway.* (если удалось разобрать)."""

    code: str
    message: str
    details: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    @property
    def user_message(self) -> str:
        """Сообщение без внутренних деталей — для ответов пользователю/LLM."""

        description = GATEWAY_ERRORS.get(self.code, self.message)
        return f"{USER_FRIENDLY_ISS_ERROR} ({description})"


class IssPlusTimeoutError(IssPlusError):
    """Таймаут ожидания ответа ISS+."""

    def __init__(self, message: str = "Таймаут ожидания ответа ISS+") -> None:
        super().__init__(code="gateway.timeout", message=message)


class IssPlusValidationError(ValueError):
    """Некорректные входные параметры MCP tool (в т.ч. selector injection)."""


def extract_gateway_code(text: str) -> str | None:
    """Найти код gateway.* в тексте ERROR-фрейма."""

    lowered = text.lower()
    for code in GATEWAY_ERRORS:
        if code in lowered:
            return code
    return None


def classify_iss_error(message_header: str, body: str, extra_headers: dict[str, str] | None = None) -> IssPlusError:
    """Построить IssPlusError из заголовка/тела STOMP ERROR."""

    extra = extra_headers or {}
    blob = f"{message_header}\n{body}\n{extra.get('error-code', '')}\n{extra.get('message', '')}"
    code = extract_gateway_code(blob)
    if code is None:
        lowered = blob.lower()
        if "access denied" in lowered or "access-denied" in lowered:
            code = "gateway.access-denied"
        else:
            code = "gateway.exception"
    description = GATEWAY_ERRORS.get(code, "Непредвиденная ошибка на сервере доступа")
    return IssPlusError(code=code, message=description, details="")
