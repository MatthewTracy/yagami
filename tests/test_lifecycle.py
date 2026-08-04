from __future__ import annotations

import pytest

from yagami import config as config_mod
from yagami.api import config as config_api
from yagami.backends.ollama import OllamaBackend
from yagami.chat import stream as stream_mod
from yagami.main import build_app
from yagami.storage.db import get_db


@pytest.mark.asyncio
async def test_lifespan_cleans_up_after_application_error(tmp_path, monkeypatch):
    monkeypatch.setenv("YAGAMI_CONFIG_PATH", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("YAGAMI_DB_PATH", str(tmp_path / "lifecycle.db"))
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
    app = build_app()

    with pytest.raises(RuntimeError, match="simulated app failure"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("simulated app failure")

    with pytest.raises(RuntimeError, match="DB not opened"):
        get_db()
    assert stream_mod._memory_worker is None
    assert stream_mod._retriever is None

    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(("installed", "expected"), [(True, "ollama"), (False, "echo")])
async def test_demo_prefers_an_installed_ollama_model(
    tmp_path, monkeypatch, installed: bool, expected: str
) -> None:
    async def has_model(_self, _model=None) -> bool:
        return installed

    monkeypatch.setattr(OllamaBackend, "has_model", has_model)
    monkeypatch.setenv("YAGAMI_DEMO_MODE", "true")
    monkeypatch.setenv("YAGAMI_REQUIRE_AUTH", "false")
    monkeypatch.setenv("YAGAMI_CONFIG_PATH", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("YAGAMI_DB_PATH", str(tmp_path / f"demo-{expected}.db"))
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
    app = build_app()

    async with app.router.lifespan_context(app):
        assert config_api._policy is not None
        assert config_api._policy.config.default_backend == expected

    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
