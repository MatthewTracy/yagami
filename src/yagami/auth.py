from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings

_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_bearer = HTTPBearer(auto_error=False)
DEFAULT_SCOPES = frozenset(
    {
        "gateway:invoke",
        "gateway:read",
        "policy:read",
        "policy:preview",
        "policy:replay",
        "privacy:transform",
        "audit:read",
        "metrics:read",
    }
)


@dataclass(frozen=True)
class KeyRecord:
    project_id: str
    key: str
    roles: frozenset[str]
    scopes: frozenset[str]


@dataclass(frozen=True)
class Principal:
    project_id: str
    key_fingerprint: str | None
    authenticated: bool
    roles: frozenset[str] = frozenset({"local-admin"})
    scopes: frozenset[str] = DEFAULT_SCOPES


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _parse_api_keys(raw: str) -> list[KeyRecord]:
    """Parse JSON or ``project:key,project-two:key`` without logging secrets."""
    value = raw.strip()
    if not value:
        return []
    if value.startswith("{"):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("YAGAMI_API_KEYS contains invalid JSON") from exc
        if not isinstance(loaded, dict):
            raise ValueError("YAGAMI_API_KEYS JSON must be an object of project IDs to keys")
        records: list[KeyRecord] = []
        for project, definition in loaded.items():
            definitions = definition if isinstance(definition, list) else [definition]
            if not definitions:
                raise ValueError(f"API key definitions for {project!r} cannot be empty")
            for item in definitions:
                if isinstance(item, str):
                    key = item
                    roles = frozenset({"service"})
                    scopes = DEFAULT_SCOPES
                elif isinstance(item, dict):
                    key = item.get("key")
                    if not isinstance(key, str):
                        raise ValueError(
                            f"API key definition for {project!r} requires a string key"
                        )
                    raw_roles = item.get("roles", ["service"])
                    raw_scopes = item.get("scopes", sorted(DEFAULT_SCOPES))
                    if not isinstance(raw_roles, list) or not all(
                        isinstance(role, str) for role in raw_roles
                    ):
                        raise ValueError(f"roles for project {project!r} must be a string list")
                    if not isinstance(raw_scopes, list) or not all(
                        isinstance(scope, str) for scope in raw_scopes
                    ):
                        raise ValueError(f"scopes for project {project!r} must be a string list")
                    roles = frozenset(raw_roles)
                    scopes = frozenset(raw_scopes)
                else:
                    raise ValueError(
                        f"API key definition for {project!r} must be a string or object"
                    )
                records.append(
                    KeyRecord(
                        project_id=str(project),
                        key=key,
                        roles=roles,
                        scopes=scopes,
                    )
                )
    else:
        records = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            separator = ":" if ":" in item else "="
            if separator not in item:
                raise ValueError("YAGAMI_API_KEYS entries must use project:key")
            project, key = item.split(separator, 1)
            records.append(
                KeyRecord(
                    project_id=project.strip(),
                    key=key.strip(),
                    roles=frozenset({"service"}),
                    scopes=DEFAULT_SCOPES,
                )
            )

    fingerprints: set[str] = set()
    for record in records:
        project = record.project_id
        key = record.key
        if not _PROJECT_ID.fullmatch(project):
            raise ValueError(f"invalid YAGAMI_API_KEYS project ID {project!r}")
        if len(key) < 16:
            raise ValueError(f"API key for project {project!r} must be at least 16 characters")
        fingerprint = _fingerprint(key)
        if fingerprint in fingerprints:
            raise ValueError("the same API key cannot be assigned to multiple projects")
        fingerprints.add(fingerprint)
    return records


class Authenticator:
    def __init__(self, settings: Settings) -> None:
        keys = _parse_api_keys(settings.api_keys)
        if settings.require_auth and not keys:
            raise ValueError("YAGAMI_REQUIRE_AUTH is true but YAGAMI_API_KEYS is empty")
        self._keys = keys
        self.required = settings.require_auth or bool(keys)

    def authenticate(self, token: str | None) -> Principal:
        if not self.required:
            return Principal(project_id="local", key_fingerprint=None, authenticated=False)
        if not token:
            raise HTTPException(
                status_code=401,
                detail="missing bearer API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        for record in self._keys:
            if hmac.compare_digest(token, record.key):
                return Principal(
                    project_id=record.project_id,
                    key_fingerprint=_fingerprint(record.key),
                    authenticated=True,
                    roles=record.roles,
                    scopes=record.scopes,
                )
        raise HTTPException(
            status_code=401,
            detail="invalid bearer API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> Principal:
    authenticator: Authenticator = request.app.state.runtime.authenticator
    token = credentials.credentials if credentials is not None else None
    return authenticator.authenticate(token)


def require_scope(scope: str):
    async def dependency(principal: Principal = Depends(require_principal)) -> Principal:
        if scope not in principal.scopes and "local-admin" not in principal.roles:
            raise HTTPException(
                status_code=403,
                detail=f"API key lacks required scope {scope!r}",
            )
        return principal

    return dependency


__all__ = [
    "Authenticator",
    "DEFAULT_SCOPES",
    "Principal",
    "require_principal",
    "require_scope",
]
