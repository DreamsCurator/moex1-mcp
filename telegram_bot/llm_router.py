"""Маршрутизация: сообщение пользователя → OpenAI tool calling → MCP → финальный ответ."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI

from telegram_bot.prompts import (
    FALLBACK_MESSAGE,
    SYSTEM_PROMPT_CANARY,
    USER_FACING_ERROR,
    system_prompt,
    wrap_tool_result,
)
from telegram_bot.sanitize import redact_secrets

log = logging.getLogger("telegram.llm")
error_log = logging.getLogger("telegram.errors")
MAX_TOOL_ROUNDS = 3


class ToolCaller(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


@dataclass
class RouteResult:
    """Результат обработки пользовательского текста."""

    text: str
    used_tool: bool
    tool_name: str | None = None
    fallback: bool = False
    unknown_model: bool = False


def mcp_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Преобразовать MCP tools/list в OpenAI Chat Completions tools."""

    converted: list[dict[str, Any]] = []
    for tool in tools:
        name = tool.get("name")
        if not name:
            continue
        parameters = tool.get("inputSchema") or tool.get("input_schema") or {
            "type": "object",
            "properties": {},
        }
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description") or "",
                    "parameters": parameters,
                },
            }
        )
    return converted


def is_unknown_model_error(exc: BaseException) -> bool:
    """Ошибка API из-за неизвестной модели (не подменяем модель молча)."""

    text = str(exc).lower()
    markers = (
        "model_not_found",
        "does not exist",
        "invalid model",
        "unknown model",
        "not found",
        "model_not_available",
    )
    return "model" in text and any(marker in text for marker in markers)


def _message_tool_calls(message: Any) -> list[Any]:
    calls = getattr(message, "tool_calls", None) or []
    return list(calls)


class LlmRouter:
    """Два вызова LLM: выбор tool и формулировка ответа по данным биржи."""

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        model: str,
        tools: list[dict[str, Any]],
        mcp: ToolCaller,
        *,
        secrets: list[str] | None = None,
    ) -> None:
        self._openai = openai_client
        self._model = model
        self._openai_tools = mcp_tools_to_openai(tools)
        self._mcp = mcp
        self._secrets = secrets or []

    async def handle_user_message(self, user_text: str) -> RouteResult:
        """Обработать текст пользователя; при отсутствии tool — фиксированный fallback."""

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": user_text},
        ]
        used_name: str | None = None
        try:
            for round_idx in range(MAX_TOOL_ROUNDS):
                first = await self._chat(messages, use_tools=True)
                message = first.choices[0].message
                tool_calls = _message_tool_calls(message)
                if not tool_calls:
                    if used_name is None:
                        log.info("llm_no_tool")
                        return RouteResult(text=FALLBACK_MESSAGE, used_tool=False, fallback=True)
                    final = (message.content or "").strip()
                    final = redact_secrets(final, self._secrets)
                    if not final or SYSTEM_PROMPT_CANARY in final:
                        return RouteResult(
                            text=FALLBACK_MESSAGE,
                            used_tool=True,
                            tool_name=used_name,
                            fallback=True,
                        )
                    log.info("llm_final_answer tool=%s", used_name)
                    return RouteResult(text=final, used_tool=True, tool_name=used_name)

                assistant_tool_calls = []
                tool_messages: list[dict[str, Any]] = []
                for call in tool_calls:
                    fn = call.function
                    name = fn.name
                    used_name = name
                    try:
                        arguments = json.loads(fn.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                    log.info("llm_chose_tool name=%s round=%s", name, round_idx + 1)
                    try:
                        result = await self._mcp.call_tool(name, arguments)
                    except Exception:
                        error_log.exception("mcp_tool_failed name=%s", name)
                        result = {
                            "ok": False,
                            "error_code": "mcp_error",
                            "error": "tool failed",
                        }
                    payload = json.dumps(result, ensure_ascii=False)
                    assistant_tool_calls.append(
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": name, "arguments": fn.arguments or "{}"},
                        }
                    )
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": wrap_tool_result(payload),
                        }
                    )
                messages.append({"role": "assistant", "tool_calls": assistant_tool_calls})
                messages.extend(tool_messages)

            second = await self._chat(messages, use_tools=False)
        except Exception as exc:
            if is_unknown_model_error(exc):
                error_log.error(
                    "OpenAI API rejected model %s: %s — not substituting another model",
                    self._model,
                    type(exc).__name__,
                )
                return RouteResult(
                    text=USER_FACING_ERROR,
                    used_tool=False,
                    fallback=True,
                    unknown_model=True,
                )
            error_log.exception("llm_call_failed")
            return RouteResult(text=USER_FACING_ERROR, used_tool=bool(used_name), tool_name=used_name, fallback=True)

        final = (second.choices[0].message.content or "").strip()
        final = redact_secrets(final, self._secrets)
        if not final or SYSTEM_PROMPT_CANARY in final:
            return RouteResult(
                text=FALLBACK_MESSAGE,
                used_tool=True,
                tool_name=used_name,
                fallback=True,
            )
        log.info("llm_final_answer tool=%s", used_name)
        return RouteResult(text=final, used_tool=True, tool_name=used_name)

    async def _chat(self, messages: list[dict[str, Any]], *, use_tools: bool) -> Any:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            # gpt-5.6-luna в /v1/chat/completions не принимает function tools
            # при ненулевом reasoning_effort — модель не подменяем.
            "reasoning_effort": "none",
        }
        if use_tools and self._openai_tools:
            kwargs["tools"] = self._openai_tools
            kwargs["tool_choice"] = "auto"
        try:
            return await self._openai.chat.completions.create(**kwargs)
        except TypeError:
            kwargs.pop("reasoning_effort", None)
            return await self._openai.chat.completions.create(**kwargs)
