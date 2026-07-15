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
from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from .api import config as config_api
from .api import costs as costs_api
from .api import decisions as decisions_api
from .api import ingest as ingest_api
from .api import kb as kb_api
from .api import mcp as mcp_api
from .api import memory as memory_api
from .api import openai_compat as openai_compat_api
from .api import privacy as privacy_api
from .api import sessions as sessions_api
from .api import stats as stats_api
from .api import tool_schemas as tool_schemas_api
from .backends.registry import build_all
from .auth import Authenticator, Principal, require_admin, require_scope
from .chat.session import SessionStore
from .chat.stream import chat_endpoint, set_memory_worker, set_retriever
from .memory.embedder import Embedder
from .memory.retriever import Retriever
from .memory.worker import EmbeddingWorker
from .middleware import RequestSizeLimitMiddleware
from .mcp_gateway import build_mcp_server
from .paths import configure_default_state, project_root, ui_dist
from .privacy import cleanup_expired_sessions
from . import secrets
from .config import effective_routing, get_config, get_settings
from .gateway import GatewayService
from .key_management import resolve_secret
from .governance import ApprovalNotifier, ApprovalStore, PrivacyTransformer, ToolSchemaRegistry
from .governance.presidio import PresidioInspector
from .policy import PolicyEngine
from .projects import ProjectGovernor, ProjectRegistry
from .router.classifier import OllamaJSONClassifier
from .router.policy import RoutingPolicy
from .skills import mcp_manager as mcp_manager_mod
from .skills.mcp_manager import McpManager
from .storage.db import close_db, open_db
from .runtime import AppRuntime
from .telemetry.observability import GatewayMetrics
from .telemetry.audit import AuditLedger, HttpAuditSink
from . import __version__

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yagami")

# Installed wheels use ~/.yagami after `yagami init`; source checkouts keep
# their repository-local config unless the operator explicitly overrides it.
configure_default_state()


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


def _log_safe(value: str | None, *, limit: int = 512) -> str:
    if value is None:
        return "<missing>"
    return value.replace("\r", "\\r").replace("\n", "\\n")[:limit]


def build_app() -> FastAPI:
    # Load .env into os.environ so secrets.get() can fall back to env vars
    # when the OS keyring doesn't have the value.
    load_dotenv()
    cfg = get_config()
    settings = get_settings()  # also picks up YAGAMI_* env overrides for non-secret config
    if settings.demo_mode:
        cfg.routing.default_backend = "echo"
        cfg.routing.block_cloud = True
        cfg.memory.enabled = False
    sessions = SessionStore()
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        db_path = project_root() / db_path

    # Backend registry: discovers every module under yagami.backends/, calls
    # each one's build(cfg, secrets.get) and keeps the non-None results.
    # See backends/registry.py - adding a new backend is one new file, no
    # main.py edit.
    backends = build_all(cfg, secrets.get)
    expected = {"ollama", "echo", "anthropic", "stability", "openai", "llama_cpp"}
    if cfg.foundry_local.enabled:
        expected.add("foundry_local")
    missing = expected - set(backends.keys())
    if missing:
        log.info("backends not loaded: %s (missing key or model)", sorted(missing))
    log.info("backends loaded: %s", sorted(backends.keys()))

    classifier = None if settings.demo_mode else OllamaJSONClassifier(cfg.ollama)
    presidio = (
        PresidioInspector(
            settings.presidio_url,
            language=settings.presidio_language,
            score_threshold=settings.presidio_score_threshold,
            timeout_seconds=settings.presidio_timeout_seconds,
            fail_closed=settings.presidio_fail_closed,
            bearer_token=resolve_secret(
                settings.presidio_token,
                settings.presidio_token_ref,
                label="YAGAMI_PRESIDIO_TOKEN_REF",
            ),
            allow_remote=settings.presidio_allow_remote,
        )
        if settings.presidio_url
        else None
    )
    policy = RoutingPolicy(
        config=effective_routing(cfg),
        backends=backends,
        classifier=classifier,
        sensitivity_inspector=presidio,
    )
    config_api.set_policy(policy)
    policy_path = Path(settings.policy_path)
    if not policy_path.is_absolute():
        policy_path = project_root() / policy_path
    policy_engine = PolicyEngine(policy_path)
    projects_path = Path(settings.projects_path)
    if not projects_path.is_absolute():
        projects_path = project_root() / projects_path
    projects = ProjectRegistry(projects_path)
    governor = ProjectGovernor(projects)
    authenticator = Authenticator(settings)
    metrics = GatewayMetrics()
    audit_key = resolve_secret(
        settings.audit_key, settings.audit_key_ref, label="YAGAMI_AUDIT_KEY_REF"
    )
    sink_token = resolve_secret(
        settings.audit_sink_token,
        settings.audit_sink_token_ref,
        label="YAGAMI_AUDIT_SINK_TOKEN_REF",
    )
    audit_sink = (
        HttpAuditSink(
            settings.audit_sink_url,
            token=sink_token,
            sink_format=settings.audit_sink_format,
            timeout_seconds=settings.audit_sink_timeout_seconds,
        )
        if settings.audit_sink_url
        else None
    )
    audit = AuditLedger(
        key=audit_key,
        required=settings.audit_required,
        sink=audit_sink,
        sink_required=settings.audit_sink_required,
    )
    approval_notifier = (
        ApprovalNotifier(
            settings.approval_webhook_url,
            format=settings.approval_webhook_format,
            timeout_seconds=settings.approval_webhook_timeout_seconds,
        )
        if settings.approval_webhook_url
        else None
    )
    approvals = ApprovalStore(approval_notifier)
    tool_schemas = ToolSchemaRegistry()
    transformer = PrivacyTransformer(
        key=resolve_secret(
            settings.transform_key,
            settings.transform_key_ref,
            label="YAGAMI_TRANSFORM_KEY_REF",
        ),
        ttl_seconds=settings.transform_vault_ttl_seconds,
    )
    gateway = GatewayService(
        routing_policy=policy,
        backends=backends,
        policy_engine=policy_engine,
        sessions=sessions,
        metrics=metrics,
        transformer=transformer,
        governor=governor,
        audit=audit,
        approvals=approvals,
        tool_schemas=tool_schemas,
    )
    mcp_http_app = None
    mcp_endpoint = None
    if settings.mcp_server_enabled:
        _mcp_server, mcp_http_app, mcp_endpoint = build_mcp_server(gateway, authenticator)

    embedding_worker: EmbeddingWorker | None = None
    embedder: Embedder | None = None
    mcp_manager: McpManager | None = None
    retention_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal embedding_worker, embedder, mcp_manager, retention_task
        mcp_lifespan_task: asyncio.Task | None = None
        mcp_lifespan_stop = asyncio.Event()
        await open_db(db_path)
        try:
            expired_tokens = await transformer.cleanup_expired()
            if expired_tokens:
                log.info("privacy transform vault: removed %d expired token(s)", expired_tokens)
            expired_approvals = await approvals.cleanup_expired()
            if expired_approvals:
                log.info("tool approvals: removed %d expired approval(s)", expired_approvals)
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
            if mcp_http_app is not None:
                mcp_lifespan_ready = asyncio.Event()
                mcp_lifespan_errors: list[BaseException] = []

                async def run_mcp_lifespan() -> None:
                    try:
                        async with mcp_http_app.router.lifespan_context(mcp_http_app):
                            mcp_lifespan_ready.set()
                            await mcp_lifespan_stop.wait()
                    except BaseException as exc:
                        mcp_lifespan_errors.append(exc)
                        mcp_lifespan_ready.set()
                        raise

                mcp_lifespan_task = asyncio.create_task(run_mcp_lifespan())
                await mcp_lifespan_ready.wait()
                if mcp_lifespan_errors:
                    await asyncio.gather(mcp_lifespan_task, return_exceptions=True)
                    raise mcp_lifespan_errors[0]
            yield
        finally:
            if mcp_lifespan_task is not None:
                mcp_lifespan_stop.set()
                await asyncio.gather(mcp_lifespan_task, return_exceptions=True)
            await kb_api.shutdown_jobs()
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
            resources: list[tuple[str, object]] = [("classifier", classifier), *backends.items()]
            if presidio is not None:
                resources.append(("presidio", presidio))
            if embedder is not None:
                resources.append(("embedder", embedder))
            await asyncio.gather(*(_close_resource(name, resource) for name, resource in resources))
            await close_db()

    app = FastAPI(
        title="Yagami Private AI Gateway",
        version=__version__,
        description=("Policy-governed routing across local models, cloud LLMs, memory, and tools."),
        lifespan=lifespan,
        docs_url=None if settings.headless else "/docs",
        redoc_url=None if settings.headless else "/redoc",
        openapi_url=None if settings.headless else "/openapi.json",
    )
    app.state.runtime = AppRuntime(
        settings=settings,
        config=cfg,
        backends=backends,
        routing_policy=policy,
        policy_engine=policy_engine,
        sessions=sessions,
        authenticator=authenticator,
        metrics=metrics,
        transformer=transformer,
        approvals=approvals,
        tool_schemas=tool_schemas,
        projects=projects,
        governor=governor,
        audit=audit,
        gateway=gateway,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_bytes)

    app.include_router(openai_compat_api.router)
    if mcp_endpoint is not None:
        app.router.routes.append(
            Route("/mcp", endpoint=mcp_endpoint, methods=None, include_in_schema=False)
        )
    if not settings.headless:
        admin_dependencies = [Depends(require_admin)]
        app.include_router(decisions_api.router, dependencies=admin_dependencies)
        app.include_router(sessions_api.router, dependencies=admin_dependencies)
        app.include_router(costs_api.router, dependencies=admin_dependencies)
        app.include_router(ingest_api.router, dependencies=admin_dependencies)
        app.include_router(stats_api.router, dependencies=admin_dependencies)
        app.include_router(config_api.router, dependencies=admin_dependencies)
        app.include_router(memory_api.router, dependencies=admin_dependencies)
        app.include_router(privacy_api.router, dependencies=admin_dependencies)
        app.include_router(kb_api.router, dependencies=admin_dependencies)
        app.include_router(mcp_api.router, dependencies=admin_dependencies)
        app.include_router(tool_schemas_api.router, dependencies=admin_dependencies)

    @app.get("/healthz", tags=["operations"])
    async def healthz() -> dict:
        return {"ok": True, "version": __version__}

    if settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def prometheus_metrics(
            _principal: Principal = Depends(require_scope("metrics:read")),
        ):
            from fastapi import Response
            from prometheus_client import CONTENT_TYPE_LATEST

            return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)

    if not settings.headless:

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

    if not settings.headless:

        @app.websocket("/ws/chat")
        async def ws_chat(ws: WebSocket) -> None:
            origin = ws.headers.get("origin")
            if not _is_allowed_websocket_origin(origin):
                log.warning("rejected WebSocket connection from origin %s", _log_safe(origin))
                await ws.close(code=1008, reason="untrusted origin")
                return
            await chat_endpoint(ws, sessions, gateway)

    dist = ui_dist()
    if not settings.headless and dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app


app = build_app()
