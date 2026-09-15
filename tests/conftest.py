from dotenv import load_dotenv

load_dotenv()

import os
from pathlib import Path

os.environ.setdefault("MOEX_LOGIN", "test-login")
os.environ.setdefault("MOEX_PASSCODE", "test-passcode-secret")
os.environ.setdefault("MCP_API_KEY", "test-mcp-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-5.6-luna")
os.environ.setdefault("MCP_SERVER_URL", "http://127.0.0.1:8765")
os.environ.setdefault("MCP_SKIP_MOEX_CONNECT", "true")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("LOG_DIR", str(Path("logs")))

import pytest

from mcp_server.config import get_settings as mcp_get_settings
from telegram_bot.config import get_settings as bot_get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    mcp_get_settings.cache_clear()
    bot_get_settings.cache_clear()
    yield
    mcp_get_settings.cache_clear()
    bot_get_settings.cache_clear()
