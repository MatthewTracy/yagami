import pytest
from httpx import ASGITransport, AsyncClient

from yagami.cli import _is_loopback_host
from yagami import config as config_mod
from yagami.config import Settings
from yagami.main import _admin_origins, _is_allowed_websocket_origin, _log_safe, build_app


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


def test_untrusted_log_values_are_single_line_and_bounded() -> None:
    value = _log_safe("https://attacker.example\r\nforged=true" + "x" * 1_000)
    assert "\r" not in value
    assert "\n" not in value
    assert "\\r\\n" in value
    assert len(value) == 512


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


def test_headless_mode_does_not_register_local_chat_or_schema_routes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YAGAMI_HEADLESS", "true")
    monkeypatch.setenv("YAGAMI_REQUIRE_AUTH", "true")
    monkeypatch.setenv("YAGAMI_API_KEYS", "test:0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("YAGAMI_CONFIG_PATH", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("YAGAMI_POLICY_PATH", str(tmp_path / "missing-policy.yaml"))
    monkeypatch.setenv("YAGAMI_PROJECTS_PATH", str(tmp_path / "missing-projects.yaml"))
    monkeypatch.setenv("YAGAMI_DB_PATH", str(tmp_path / "headless.db"))
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
    try:
        app = build_app()
        paths = {path for route in app.routes if (path := getattr(route, "path", None))}
        assert "/ws/chat" not in paths
        assert "/api/health" not in paths
        assert "/docs" not in paths
        assert "/openapi.json" not in paths
    finally:
        config_mod.get_settings.cache_clear()
        config_mod.get_config.cache_clear()


def test_remote_admin_requires_oidc_and_explicit_https_origins() -> None:
    with pytest.raises(ValueError, match="OIDC"):
        _admin_origins(Settings(remote_admin_enabled=True, admin_allowed_origins="https://a.test"))
    with pytest.raises(ValueError, match="at least one"):
        _admin_origins(
            Settings(
                remote_admin_enabled=True,
                oidc_issuer="https://identity.test",
            )
        )
    with pytest.raises(ValueError, match="HTTPS"):
        _admin_origins(
            Settings(
                remote_admin_enabled=True,
                oidc_issuer="https://identity.test",
                admin_allowed_origins="http://admin.test",
            )
        )
    assert _admin_origins(
        Settings(
            remote_admin_enabled=True,
            oidc_issuer="https://identity.test",
            admin_allowed_origins="https://admin.test",
        )
    ) == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://admin.test",
    ]


@pytest.mark.asyncio
async def test_remote_admin_registers_headless_routes_but_requires_identity(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("YAGAMI_HEADLESS", "true")
    monkeypatch.setenv("YAGAMI_REMOTE_ADMIN_ENABLED", "true")
    monkeypatch.setenv("YAGAMI_ADMIN_ALLOWED_ORIGINS", "https://admin.test")
    monkeypatch.setenv("YAGAMI_OIDC_ISSUER", "https://identity.test")
    monkeypatch.setenv("YAGAMI_OIDC_JWKS_URL", "https://identity.test/jwks.json")
    monkeypatch.setenv("YAGAMI_CONFIG_PATH", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("YAGAMI_POLICY_PATH", str(tmp_path / "missing-policy.yaml"))
    monkeypatch.setenv("YAGAMI_PROJECTS_PATH", str(tmp_path / "missing-projects.yaml"))
    monkeypatch.setenv("YAGAMI_DB_PATH", str(tmp_path / "remote-admin.db"))
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
    try:
        app = build_app()
        paths = {path for route in app.routes if (path := getattr(route, "path", None))}
        assert "/api/health" in paths
        assert "/ws/chat" not in paths
        transport = ASGITransport(app=app, client=("203.0.113.10", 43123))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/api/health")
            config = await client.get("/api/config")
        assert health.status_code == 401
        assert config.status_code == 401
    finally:
        config_mod.get_settings.cache_clear()
        config_mod.get_config.cache_clear()
