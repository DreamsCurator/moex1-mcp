from mcp_server.stomp_frames import (
    StompFrame,
    connect_frame,
    parse_frames,
    request_frame,
    subscribe_frame,
    unsubscribe_frame,
)


def test_encode_connect_contains_domain_and_nul() -> None:
    raw = connect_frame("user", "secret")
    assert raw.endswith("\x00")
    assert "CONNECT\n" in raw
    assert "domain:passport" in raw
    assert "login:user" in raw
    demo = connect_frame("guest", "guest", domain="DEMO")
    assert "domain:DEMO" in demo
    assert "login:guest" in demo
    assert "passcode:secret" in raw


def test_parse_connected_frame() -> None:
    raw = "CONNECTED\nversion:1.2\nheart-beat:10000,10000\n\n\x00"
    frames = parse_frames(raw)
    assert len(frames) == 1
    assert frames[0].command == "CONNECTED"
    assert frames[0].headers["version"] == "1.2"
    assert frames[0].headers["heart-beat"] == "10000,10000"


def test_parse_multiple_frames_and_heartbeat() -> None:
    first = StompFrame("MESSAGE", {"id": "1"}, '{"a":1}').encode()
    raw = "\n" + first + "\n" + StompFrame("ERROR", {"message": "gateway.timeout"}).encode()
    frames = parse_frames(raw)
    assert [f.command for f in frames] == ["MESSAGE", "ERROR"]
    assert frames[0].body == '{"a":1}'
    assert frames[1].headers["message"] == "gateway.timeout"


def test_parse_bytes_and_missing_nul() -> None:
    frames = parse_frames(b"MESSAGE\nsubscription:abc\n\n{\"x\":1}")
    assert frames[0].command == "MESSAGE"
    assert frames[0].headers["subscription"] == "abc"
    assert frames[0].body == '{"x":1}'


def test_subscribe_unsubscribe_request_roundtrip() -> None:
    sub = subscribe_frame("sid", "MXSE.securities", 'TICKER="MXSE.TQBR.SBER"')
    assert "SUBSCRIBE" in sub
    assert "destination:MXSE.securities" in sub
    unsub = unsubscribe_frame("sid")
    assert "UNSUBSCRIBE" in unsub
    req = request_frame("rid", "SEARCH.ticker", 'pattern="SBER"')
    parsed = parse_frames(req)
    assert parsed[0].command == "REQUEST"
    assert parsed[0].headers["destination"] == "SEARCH.ticker"


def test_header_injection_stripped_from_values() -> None:
    frame = StompFrame("SUBSCRIBE", {"selector": "a\nb:evil"}, "")
    encoded = frame.encode()
    assert "\nb:evil" not in encoded
    assert "a b:evil" in encoded or "a b:evil" in encoded.replace("\n", " ")
