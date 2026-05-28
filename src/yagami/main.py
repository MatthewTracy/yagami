from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yagami")


def build_app() -> FastAPI:
    cfg = get_config()
    settings = get_settings()
    sessions = SessionStore()

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

    app = FastAPI(title="Yagami", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    dist = Path(__file__).resolve().parents[2] / "ui" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app


app = build_app()
