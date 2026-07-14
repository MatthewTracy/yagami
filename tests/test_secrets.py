from __future__ import annotations

from yagami import secrets


def test_headless_mode_uses_environment_without_keyring(monkeypatch):
    monkeypatch.setenv("YAGAMI_HEADLESS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setattr(
        "keyring.get_password",
        lambda *_args: (_ for _ in ()).throw(AssertionError("keyring should not be called")),
    )
    secrets.get.cache_clear()

    assert secrets.get("OPENAI_API_KEY") == "environment-key"


def test_keyring_value_takes_precedence_for_desktop_mode(monkeypatch):
    monkeypatch.delenv("YAGAMI_HEADLESS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setattr(secrets, "_backend_available", lambda: True)
    monkeypatch.setattr("keyring.get_password", lambda *_args: "keyring-key")
    secrets.get.cache_clear()

    assert secrets.get("OPENAI_API_KEY") == "keyring-key"
