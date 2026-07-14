from __future__ import annotations

import asyncio
import inspect
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import Iterable
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import config as config_api
from .api import costs as costs_api
from .api import decisions as decisions_api
from .api import ingest as ingest_api
from .api import kb as kb_api
from .api import mcp as mcp_api
from .api import memory as memory_api
from .api import privacy as privacy_api
from .api import sessions as sessions_api
from .api import stats as stats_api
from .backends.registry import build_all
from .chat.session import SessionStore
from .chat.stream import chat_endpoint, set_memory_worker, set_retriever
from .memory.embedder import Embedder
from .memory.retriever import Retriever
from .memory.worker import EmbeddingWorker
from .privacy import cleanup_expired_sessions
from . import secrets
from .config import effective_routing, get_config, get_settings
from .router.classifier import OllamaJSONClassifier
from .router.policy import RoutingPolicy
from .skills import mcp_manager as mcp_manager_mod
from .skills.mcp_manager import McpManager
from .storage.db import close_db, open_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yagami")


def _normalize_web_origin(origin: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname, port


def _is_allowed_websocket_origin(
    origin: str | None, trusted_origins: Iterable[str] | None = None
) -> bool:
    """Allow browser chat connections only from Yagami's local UI.

    Browser WebSockets are not governed by CORS. Browsers do, however, send
    an Origin header, so checking it prevents a hostile web page from opening
    a socket to a locally running Yagami instance. Non-browser clients such as
    the evaluation scripts omit Origin and remain supported.
    """
    if origin is None:
        return True
    normalized = _normalize_web_origin(origin)
    if normalized is None:
        return False
    if normalized[1] in {"localhost", "127.0.0.1", "::1"}:
        return True
    if trusted_origins is None:
        trusted_origins = os.getenv("YAGAMI_TRUSTED_ORIGINS", "").split(",")
    normalized_trusted = {
        value
        for candidate in trusted_origins
        if (value := _normalize_web_origin(candidate.strip())) is not None
    }
    return normalized in normalized_trusted


async def _close_resource(name: str, resource: object) -> None:
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 - shutdown must continue for other resources
        log.exception("failed to close %s", name)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_app() -> FastAPI:
    # Load .env into os.environ so secrets.get() can fall back to env vars
    # when the OS keyring doesn't have the value.
    load_dotenv()
    cfg = get_config()
    settings = get_settings()  # also picks up YAGAMI_* env overrides for non-secret config
    sessions = SessionStore()
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        db_path = _project_root() / db_path

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
    policy = RoutingPolicy(config=effective_routing(cfg), backends=backends, classifier=classifier)
    config_api.set_policy(policy)

    embedding_worker: EmbeddingWorker | None = None
    embedder: Embedder | None = None
    mcp_manager: McpManager | None = None
    retention_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal embedding_worker, embedder, mcp_manager, retention_task
        await open_db(db_path)
        try:
            sessions_api.set_store(sessions)
            set_memory_worker(None)
            set_retriever(None)
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
            if cfg.mcp_servers:
                mcp_manager = McpManager()
                await mcp_manager.connect_all(cfg.mcp_servers)
                mcp_manager_mod.set_manager(mcp_manager)
                log.info(
                    "mcp: %d server(s) configured, %d tool(s) connected",
                    len(cfg.mcp_servers),
                    len(mcp_manager.get_skills()),
                )
            await cleanup_expired_sessions(get_config().privacy.session_retention_days)

            async def retention_loop() -> None:
                while True:
                    await asyncio.sleep(6 * 60 * 60)
                    try:
                        await cleanup_expired_sessions(get_config().privacy.session_retention_days)
                    except Exception:  # noqa: BLE001 - maintenance must not stop the app
                        log.exception("session retention cleanup failed")

            retention_task = asyncio.create_task(retention_loop())
            yield
        finally:
            if retention_task is not None:
                retention_task.cancel()
                await asyncio.gather(retention_task, return_exceptions=True)
                retention_task = None
            if mcp_manager is not None:
                try:
                    await mcp_manager.close_all()
                except Exception:  # noqa: BLE001 - continue shutdown
                    log.exception("failed to close MCP manager")
                finally:
                    mcp_manager_mod.set_manager(None)
            if embedding_worker is not None:
                try:
                    await embedding_worker.stop()
                except Exception:  # noqa: BLE001 - continue shutdown
                    log.exception("failed to stop embedding worker")
                finally:
                    set_memory_worker(None)
                    set_retriever(None)
            resources = [("classifier", classifier), *backends.items()]
            if embedder is not None:
                resources.append(("embedder", embedder))
            await asyncio.gather(*(_close_resource(name, resource) for name, resource in resources))
            await close_db()

    app = FastAPI(title="Yagami", version="0.3.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
    app.include_router(privacy_api.router)
    app.include_router(kb_api.router)
    app.include_router(mcp_api.router)

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
        current_routing = effective_routing(get_config())
        return {
            "backends": [
                {
                    "name": b.name,
                    "is_local": b.is_local,
                    "capabilities": sorted(c.value for c in b.capabilities),
                }
                for b in backends.values()
            ],
            "default": current_routing.default_backend,
        }

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        if not _is_allowed_websocket_origin(origin):
            log.warning("rejected WebSocket connection from origin %r", origin)
            await ws.close(code=1008, reason="untrusted origin")
            return
        await chat_endpoint(ws, sessions, policy)

    dist = _project_root() / "ui" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app


app = build_app()
