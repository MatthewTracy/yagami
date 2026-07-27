"""Content-free audit evidence for authenticated administrative changes."""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("yagami.admin")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class AdminAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        if request.method not in _WRITE_METHODS or not request.url.path.startswith("/api/"):
            return response
        principal = getattr(request.state, "admin_principal", None)
        if principal is None:
            return response
        route = request.scope.get("route")
        route_name = getattr(route, "name", None) or "admin-operation"
        runtime = request.app.state.runtime
        try:
            await runtime.gateway.append_audit(
                project_id=principal.project_id,
                request_id=response.headers.get("x-yagami-request-id"),
                event_type="admin.change",
                payload={
                    "operation": str(route_name),
                    "method": request.method,
                    "status_code": response.status_code,
                    "actor_fingerprint": principal.key_fingerprint,
                    "actor_roles": sorted(principal.roles),
                },
            )
        except Exception:
            log.exception("failed to record administrative audit evidence")
            if runtime.audit.required:
                raise
        return response
