from __future__ import annotations

import asyncio
import inspect
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from .admin_audit import AdminAuditMiddleware
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
from .backends.ollama import OllamaBackend
from .backends.registry import build_all
from .auth import Authenticator, Principal, require_admin, require_scope
from .chat.session import SessionStore
from .chat.stream import chat_endpoint, set_memory_worker, set_retriever
from .memory.embedder import EmbedderProtocol, build_embedder
from .memory.retriever import Retriever
from .memory.worker import EmbeddingWorker
from .middleware import RequestSizeLimitMiddleware
from .mcp_gateway import build_mcp_server, register_downstream_tools
from .paths import configure_default_state, project_root, ui_dist
from .privacy import cleanup_expired_sessions, cleanup_policy_retention
from .responses import cleanup_expired_responses
from . import secrets
from .config import Settings, effective_routing, get_config, get_settings
from .coordination import build_coordinator
from .gateway import GatewayService
from .key_management import parse_aes256_key, resolve_secret, resolve_secret_reference_map
from .governance import ApprovalNotifier, ApprovalStore, PrivacyTransformer, ToolSchemaRegistry
from .governance.presidio import PresidioInspector
from .policy import PolicyEngine
from .projects import ProjectGovernor, ProjectRegistry
from .router.classifier import build_classifier
from .router.policy import RoutingPolicy
from .skills import mcp_manager as mcp_manager_mod
from .skills.mcp_manager import McpManager
from .skills.mcp_oauth import OAuthCredentialStore
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


def _admin_origins(settings: Settings) -> list[str]:
    local = ["http://localhost:5173", "http://127.0.0.1:5173"]
    if not settings.remote_admin_enabled:
        return local
    if not settings.oidc_issuer:
        raise ValueError("remote administration requires YAGAMI_OIDC_ISSUER")
    configured = [
        value.strip() for value in settings.admin_allowed_origins.split(",") if value.strip()
    ]
    if not configured:
        raise ValueError(
            "remote administration requires at least one YAGAMI_ADMIN_ALLOWED_ORIGINS entry"
        )
    for origin in configured:
        normalized = _normalize_web_origin(origin)
        if normalized is None:
            raise ValueError(f"invalid remote administration origin {origin!r}")
        if normalized[0] != "https" and normalized[1] not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("remote administration origins must use HTTPS unless loopback")
    return [*local, *configured]


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
    admin_origins = _admin_origins(settings)
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
    ollama_backend = backends.get("ollama")
    if isinstance(ollama_backend, OllamaBackend):
        text_models = [
            cfg.ollama.model,
            *cfg.routing.local_model_overrides.values(),
            *cfg.routing.lora_variants.values(),
        ]
        classifier_url = cfg.classifier.url or cfg.ollama.url
        if cfg.classifier.provider == "ollama" and classifier_url.rstrip("/") == (
            cfg.ollama.url.rstrip("/")
        ):
            text_models.append(cfg.classifier.model or cfg.ollama.classifier_model)
        embedding_models = []
        embedding_url = cfg.memory.embedding_url or cfg.ollama.url
        if cfg.memory.embedding_provider == "ollama" and embedding_url.rstrip("/") == (
            cfg.ollama.url.rstrip("/")
        ):
            embedding_models.append(cfg.memory.embedding_model)
        ollama_backend.configure_warmup(
            text_models=text_models,
            embedding_models=embedding_models,
        )
    expected = {"ollama", "echo", "anthropic", "stability", "openai", "llama_cpp"}
    if cfg.foundry_local.enabled:
        expected.add("foundry_local")
    missing = expected - set(backends.keys())
    if missing:
        log.info("backends not loaded: %s (missing key or model)", sorted(missing))
    log.info("backends loaded: %s", sorted(backends.keys()))

    classifier = None if settings.demo_mode else build_classifier(cfg, secrets.get)
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
    policy_path = Path(settings.policy_bundle_path or settings.policy_path)
    if not policy_path.is_absolute():
        policy_path = project_root() / policy_path
    policy_public_key_path = (
        Path(settings.policy_public_key_path) if settings.policy_public_key_path else None
    )
    if policy_public_key_path is not None and not policy_public_key_path.is_absolute():
        policy_public_key_path = project_root() / policy_public_key_path
    policy_engine = PolicyEngine(
        policy_path,
        public_key_path=policy_public_key_path,
        require_signature=settings.policy_signature_required,
    )
    projects_path = Path(settings.projects_path)
    if not projects_path.is_absolute():
        projects_path = project_root() / projects_path
    projects = ProjectRegistry(projects_path)
    coordinator = build_coordinator(
        settings.coordination_url,
        prefix=settings.coordination_prefix,
    )
    governor = ProjectGovernor(
        projects,
        coordinator,
        slot_ttl_seconds=settings.coordination_slot_ttl_seconds,
    )
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
        previous_keys=settings.audit_previous_keys,
        required=settings.audit_required,
        sink=audit_sink,
        sink_required=settings.audit_sink_required,
        outbox_max_pending=settings.audit_outbox_max_pending,
        outbox_max_attempts=settings.audit_outbox_max_attempts,
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
    transform_key = resolve_secret(
        settings.transform_key,
        settings.transform_key_ref,
        label="YAGAMI_TRANSFORM_KEY_REF",
    )
    transformer = PrivacyTransformer(
        key=transform_key,
        key_id=settings.transform_key_id,
        key_epoch=settings.transform_key_epoch,
        previous_keys=resolve_secret_reference_map(
            settings.transform_previous_key_refs,
            label="YAGAMI_TRANSFORM_PREVIOUS_KEY_REFS",
        ),
        ttl_seconds=settings.transform_vault_ttl_seconds,
    )
    oauth_credentials = (
        OAuthCredentialStore(
            key_wrapper=transformer.key_wrapper,
            identity_key=parse_aes256_key(transform_key, label="YAGAMI_TRANSFORM_KEY"),
        )
        if transformer.key_wrapper is not None and transform_key
        else None
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
    mcp_server = None
    if settings.mcp_server_enabled:
        mcp_server, mcp_http_app, mcp_endpoint = build_mcp_server(gateway, authenticator)

    embedding_worker: EmbeddingWorker | None = None
    embedder: EmbedderProtocol | None = None
    mcp_manager: McpManager | None = None
    retention_task: asyncio.Task | None = None
    ollama_warmup_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal embedding_worker, embedder, mcp_manager, retention_task, ollama_warmup_task
        mcp_lifespan_task: asyncio.Task | None = None
        mcp_lifespan_stop = asyncio.Event()
        await open_db(db_path, database_url=settings.database_url)
        audit.start()
        try:
            if settings.demo_mode and isinstance(ollama_backend, OllamaBackend):
                if await ollama_backend.has_model():
                    policy.config.default_backend = "ollama"
                    log.info("demo mode selected the installed Ollama model")
                else:
                    log.info(
                        "demo mode is using the policy-only fallback; Ollama model unavailable"
                    )
            if (
                isinstance(ollama_backend, OllamaBackend)
                and cfg.ollama.preload_models
                and not settings.demo_mode
            ):
                # Warm in the background: the health/UI surface remains usable
                # while Ollama loads models, and a failed preload never blocks boot.
                ollama_warmup_task = asyncio.create_task(ollama_backend.preload_configured_models())
            expired_tokens = await transformer.cleanup_expired()
            if expired_tokens:
                log.info("privacy transform vault: removed %d expired token(s)", expired_tokens)
            expired_approvals = await approvals.cleanup_expired()
            if expired_approvals:
                log.info("tool approvals: removed %d expired approval(s)", expired_approvals)
            sessions_api.set_store(sessions)
            set_memory_worker(None)
            set_retriever(None)
            if cfg.memory.embedding_provider != "none":
                embedder = build_embedder(cfg, secrets.get)
                _app.state.runtime.embedder = embedder
            if cfg.memory.enabled:
                if embedder is not None:
                    embedding_worker = EmbeddingWorker(embedder)
                    embedding_worker.start()
                    set_memory_worker(embedding_worker)
                set_retriever(Retriever(embedder))
                log.info(
                    "memory retriever started (provider=%s, model=%s)",
                    cfg.memory.embedding_provider,
                    embedder.model if embedder is not None else "fts-only",
                )
            if cfg.mcp_servers:
                mcp_manager = McpManager(
                    schema_registry=tool_schemas,
                    oauth_credentials=oauth_credentials,
                )
                await mcp_manager.connect_all(cfg.mcp_servers)
                mcp_manager_mod.set_manager(mcp_manager)
                if mcp_server is not None:
                    registered = register_downstream_tools(mcp_server, gateway, mcp_manager)
                    log.info(
                        "mcp: registered %d governed downstream tool(s)",
                        len(registered),
                    )
                log.info(
                    "mcp: %d server(s) configured, %d tool(s) connected",
                    len(cfg.mcp_servers),
                    len(mcp_manager.get_skills()),
                )
            await cleanup_expired_sessions(get_config().privacy.session_retention_days)
            await cleanup_policy_retention()
            await cleanup_expired_responses()
            if oauth_credentials is not None:
                await oauth_credentials.cleanup_expired_states()

            async def retention_loop() -> None:
                while True:
                    await asyncio.sleep(6 * 60 * 60)
                    try:
                        await cleanup_expired_sessions(get_config().privacy.session_retention_days)
                        await cleanup_policy_retention()
                        await cleanup_expired_responses()
                        if oauth_credentials is not None:
                            await oauth_credentials.cleanup_expired_states()
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
            await audit.stop()
            if mcp_lifespan_task is not None:
                mcp_lifespan_stop.set()
                await asyncio.gather(mcp_lifespan_task, return_exceptions=True)
            await kb_api.shutdown_jobs()
            await openai_compat_api.shutdown_response_jobs()
            if retention_task is not None:
                retention_task.cancel()
                await asyncio.gather(retention_task, return_exceptions=True)
                retention_task = None
            if ollama_warmup_task is not None:
                if not ollama_warmup_task.done():
                    ollama_warmup_task.cancel()
                await asyncio.gather(ollama_warmup_task, return_exceptions=True)
                ollama_warmup_task = None
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
            _app.state.runtime.embedder = None
            await asyncio.gather(*(_close_resource(name, resource) for name, resource in resources))
            await coordinator.close()
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
        coordinator=coordinator,
        audit=audit,
        gateway=gateway,
        mcp_oauth=oauth_credentials,
        mcp_server=mcp_server,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=admin_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(AdminAuditMiddleware)

    app.include_router(openai_compat_api.router)
    if mcp_endpoint is not None:
        app.router.routes.append(
            Route("/mcp", endpoint=mcp_endpoint, methods=None, include_in_schema=False)
        )
    if not settings.headless or settings.remote_admin_enabled:
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

    if not settings.headless or settings.remote_admin_enabled:

        @app.get("/api/health")
        async def health(_principal: Principal = Depends(require_admin)) -> dict:
            model_backed_demo = settings.demo_mode and policy.config.default_backend == "ollama"
            payload: dict[str, Any] = {
                "ok": True,
                "mode": (
                    "local-model-demo"
                    if model_backed_demo
                    else "policy-only-demo"
                    if settings.demo_mode
                    else "standard"
                ),
                "demo_mode": settings.demo_mode,
                "default_backend": policy.config.default_backend,
                "message": (
                    "Local Ollama model-backed demo is active; cloud routing is disabled."
                    if model_backed_demo
                    else (
                        "Policy-only demo is active; install the configured Ollama model and restart "
                        "for AI-generated responses."
                    )
                    if settings.demo_mode
                    else "Standard model-backed mode is active."
                ),
                "backends": [
                    {"name": b.name, "is_local": b.is_local, "healthy": await b.health()}
                    for b in backends.values()
                ],
            }
            if isinstance(ollama_backend, OllamaBackend):
                payload["local_performance"] = await ollama_backend.runtime_status()
            return payload

        @app.get("/api/models")
        async def models(_principal: Principal = Depends(require_admin)) -> dict:
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
