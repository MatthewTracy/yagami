from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import decisions as decisions_api
from .api import sessions as sessions_api
from .backends.anthropic import ClaudeBackend
from .backends.base import Backend
from .backends.echo import EchoBackend
from .backends.ollama import OllamaBackend
from .backends.stability import StabilityImageBackend
from .chat.session import SessionStore
from .chat.stream import chat_endpoint
from .config import get_config, get_settings
from .router.classifier import OllamaJSONClassifier
from .router.policy import RoutingPolicy
from .storage.db import close_db, open_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yagami")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_app() -> FastAPI:
    cfg = get_config()
    settings = get_settings()
    sessions = SessionStore()
    db_path = _project_root() / "yagami.db"

    backends: dict[str, Backend] = {
        "echo": EchoBackend(),
        "ollama": OllamaBackend(cfg.ollama),
    }
    if settings.anthropic_api_key:
        backends["anthropic"] = ClaudeBackend(cfg.anthropic, settings.anthropic_api_key)
    else:
        log.warning("ANTHROPIC_API_KEY not set; Claude backend disabled")
    if settings.stability_api_key:
        backends["stability"] = StabilityImageBackend(cfg.stability, settings.stability_api_key)
    else:
        log.warning("STABILITY_API_KEY not set; Stability backend disabled")

    classifier = OllamaJSONClassifier(cfg.ollama)
    policy = RoutingPolicy(config=cfg.routing, backends=backends, classifier=classifier)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await open_db(db_path)
        sessions_api.set_store(sessions)
        yield
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
