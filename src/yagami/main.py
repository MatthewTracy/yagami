from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import config as config_api
from .api import costs as costs_api
from .api import decisions as decisions_api
from .api import ingest as ingest_api
from .api import memory as memory_api
from .api import sessions as sessions_api
from .api import stats as stats_api
from .backends.registry import build_all
from .chat.session import SessionStore
from .chat.stream import chat_endpoint, set_memory_worker, set_retriever
from .memory.embedder import Embedder
from .memory.retriever import Retriever
from .memory.worker import EmbeddingWorker
from . import secrets
from .config import get_config, get_settings
from .router.classifier import OllamaJSONClassifier
from .router.policy import RoutingPolicy
from .storage.db import close_db, open_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yagami")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_app() -> FastAPI:
    # Load .env into os.environ so secrets.get() can fall back to env vars
    # when the OS keyring doesn't have the value.
    load_dotenv()
    cfg = get_config()
    _ = get_settings()  # still picks up YAGAMI_* env overrides for non-secret config
    sessions = SessionStore()
    db_path = _project_root() / "yagami.db"

    # Backend registry: discovers every module under yagami.backends/, calls
    # each one's build(cfg, secrets.get) and keeps the non-None results.
    # See backends/registry.py - adding a new backend is one new file, no
    # main.py edit.
    backends = build_all(cfg, secrets.get)
    expected = {"ollama", "echo", "anthropic", "stability", "openai", "llama_cpp"}
    missing = expected - set(backends.keys())
    if missing:
        log.info("backends not loaded: %s (missing key or model)", sorted(missing))
    log.info("backends loaded: %s", sorted(backends.keys()))

    classifier = OllamaJSONClassifier(cfg.ollama)
    policy = RoutingPolicy(config=cfg.routing, backends=backends, classifier=classifier)

    embedding_worker: EmbeddingWorker | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal embedding_worker
        await open_db(db_path)
        sessions_api.set_store(sessions)
        if cfg.memory.enabled:
            embedder = Embedder(url=cfg.ollama.url, model=cfg.memory.embedding_model)
            embedding_worker = EmbeddingWorker(embedder)
            embedding_worker.start()
            set_memory_worker(embedding_worker)
            set_retriever(Retriever(embedder))
            log.info(
                "memory worker + retriever started (model=%s)",
                cfg.memory.embedding_model,
            )
        yield
        if embedding_worker is not None:
            await embedding_worker.stop()
        await close_db()

    app = FastAPI(title="Yagami", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(decisions_api.router)
    app.include_router(sessions_api.router)
    app.include_router(costs_api.router)
    app.include_router(ingest_api.router)
    app.include_router(stats_api.router)
    app.include_router(config_api.router)
    app.include_router(memory_api.router)

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "ok": True,
            "backends": [
                {"name": b.name, "is_local": b.is_local, "healthy": await b.health()}
                for b in backends.values()
            ],
        }

    @app.get("/api/models")
    async def models() -> dict:
        return {
            "backends": [
                {
                    "name": b.name,
                    "is_local": b.is_local,
                    "capabilities": sorted(c.value for c in b.capabilities),
                }
                for b in backends.values()
            ],
            "default": cfg.routing.default_backend,
        }

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket) -> None:
        await chat_endpoint(ws, sessions, policy)

    dist = _project_root() / "ui" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app


app = build_app()
