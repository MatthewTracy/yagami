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
from .api import sessions as sessions_api
from .api import stats as stats_api
from .backends.anthropic import ClaudeBackend
from .backends.base import Backend
from .backends.echo import EchoBackend
from .backends.ollama import OllamaBackend
from .backends.stability import StabilityImageBackend
from .chat.session import SessionStore
from .chat.stream import chat_endpoint
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

    anthropic_key = secrets.get("ANTHROPIC_API_KEY")
    stability_key = secrets.get("STABILITY_API_KEY")

    backends: dict[str, Backend] = {
        "echo": EchoBackend(),
        "ollama": OllamaBackend(cfg.ollama),
    }
    if anthropic_key:
        backends["anthropic"] = ClaudeBackend(cfg.anthropic, anthropic_key)
    else:
        log.warning("ANTHROPIC_API_KEY not in keyring or env; Claude backend disabled")
    if stability_key:
        backends["stability"] = StabilityImageBackend(cfg.stability, stability_key)
    else:
        log.warning("STABILITY_API_KEY not in keyring or env; Stability backend disabled")

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
    app.include_router(costs_api.router)
    app.include_router(ingest_api.router)
    app.include_router(stats_api.router)
    app.include_router(config_api.router)

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
