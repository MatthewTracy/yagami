from yagami.cli import _is_loopback_host
from yagami.main import _is_allowed_websocket_origin


def test_websocket_origins_allow_local_ui_and_non_browser_clients() -> None:
    assert _is_allowed_websocket_origin(None)
    assert _is_allowed_websocket_origin("http://localhost:8000")
    assert _is_allowed_websocket_origin("http://127.0.0.1:5173")
    assert _is_allowed_websocket_origin("https://[::1]:8443")


def test_websocket_origins_reject_remote_and_opaque_origins() -> None:
    assert not _is_allowed_websocket_origin("https://attacker.example")
    assert not _is_allowed_websocket_origin("null")
    assert not _is_allowed_websocket_origin("not an origin")
    assert not _is_allowed_websocket_origin("https://trusted.example/path")


def test_websocket_origin_allows_only_exact_configured_remote_origin() -> None:
    trusted = ["https://yagami.example"]
    assert _is_allowed_websocket_origin("https://yagami.example", trusted)
    assert _is_allowed_websocket_origin("https://yagami.example:443", trusted)
    assert not _is_allowed_websocket_origin("http://yagami.example", trusted)
    assert not _is_allowed_websocket_origin("https://yagami.example:8443", trusted)


def test_cli_loopback_detection() -> None:
    assert _is_loopback_host("localhost")
    assert _is_loopback_host("LOCALHOST")
    assert _is_loopback_host("127.0.0.1")
    assert _is_loopback_host("127.42.0.7")
    assert _is_loopback_host("::1")
    assert not _is_loopback_host("0.0.0.0")
    assert not _is_loopback_host("::")
    assert not _is_loopback_host("yagami.example")
