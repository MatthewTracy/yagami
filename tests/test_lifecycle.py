from __future__ import annotations

import pytest

from yagami import config as config_mod
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
