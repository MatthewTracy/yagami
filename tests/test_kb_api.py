from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from yagami import config as config_mod
from yagami.main import build_app


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Same fixture shape as test_config_api.py / test_profiles.py - point
    the app at a throwaway config + DB so tests don't touch the real ones."""
    cfg_file = tmp_path / "yagami.toml"
    src = Path("config/yagami.toml")
    if src.exists():
        shutil.copy(src, cfg_file)
    monkeypatch.setenv("YAGAMI_CONFIG_PATH", str(cfg_file))
    monkeypatch.setenv("YAGAMI_DB_PATH", str(tmp_path / "yagami.db"))
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
    yield cfg_file
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()


@pytest.mark.asyncio
async def test_index_nonexistent_folder_rejected(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/kb/index", json={"path": "/definitely/not/a/real/path"})
            assert r.status_code == 400


@pytest.mark.asyncio
async def test_index_file_instead_of_folder_rejected(tmp_config, tmp_path):
    f = tmp_path / "not_a_dir.txt"
    f.write_text("hi")
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/kb/index", json={"path": str(f)})
            assert r.status_code == 400


@pytest.mark.asyncio
async def test_list_indexed_starts_empty(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/kb")
            assert r.status_code == 200
            assert r.json() == {"sources": [], "count": 0}


@pytest.mark.asyncio
async def test_index_folder_end_to_end_without_live_ollama(tmp_config, tmp_path):
    """No live Ollama in this test environment - Embedder.embed() catches
    the connection error and returns None (see memory/embedder.py), so
    indexing should still complete (rows just land 'failed', not crash the
    request). Confirms the endpoint degrades instead of 500ing."""
    (tmp_path / "doc.txt").write_text("some indexable content")
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/kb/index", json={"path": str(tmp_path)})
            assert r.status_code == 200
            data = r.json()
            assert data["files_indexed"] == 1
            assert data["chunks_written"] == 1

            listed = await c.get("/api/kb")
            assert listed.json()["count"] == 1


@pytest.mark.asyncio
async def test_delete_nonexistent_source_404s(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.request("DELETE", "/api/kb/source", params={"path": "/no/such/file"})
            assert r.status_code == 404
