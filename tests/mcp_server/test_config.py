from mcp_server.config import McpServerSettings


def test_login_passcode_pair() -> None:
    settings = McpServerSettings.model_construct(
        moex_login="user",
        moex_passcode="secret",
        moex_algopack_token="",
    )
    assert settings.resolve_credentials() == ("user", "secret")
    assert settings.uses_opaque_algopack_token() is False
    assert settings.websocket_headers() == {}


def test_short_token_split() -> None:
    settings = McpServerSettings.model_construct(
        moex_login="",
        moex_passcode="",
        moex_algopack_token="alice:wonderland",
    )
    assert settings.resolve_credentials() == ("alice", "wonderland")


def test_opaque_token_not_split_on_colon() -> None:
    token = "x" * 90 + ":not-a-password"
    settings = McpServerSettings.model_construct(
        moex_login="",
        moex_passcode="",
        moex_algopack_token=token,
    )
    login, passcode = settings.resolve_credentials()
    assert passcode == token
    assert login
    assert settings.uses_opaque_algopack_token() is True
    assert settings.websocket_headers()["Authorization"].startswith("Bearer ")


def test_demo_login_skips_bearer_header() -> None:
    token = "x" * 90
    settings = McpServerSettings.model_construct(
        moex_login="guest",
        moex_passcode="guest",
        moex_algopack_token=token,
    )
    assert settings.resolve_credentials() == ("guest", "guest")
    assert settings.websocket_headers() == {}
