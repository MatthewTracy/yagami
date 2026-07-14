from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
from dataclasses import dataclass

import jwt
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
    subject_id: str | None = None
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
                    key_value = item.get("key")
                    if not isinstance(key_value, str):
                        raise ValueError(
                            f"API key definition for {project!r} requires a string key"
                        )
                    key = key_value
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
        if settings.oidc_issuer and not settings.oidc_jwks_url:
            raise ValueError("YAGAMI_OIDC_JWKS_URL is required when YAGAMI_OIDC_ISSUER is set")
        if settings.require_auth and not keys and not settings.oidc_issuer:
            raise ValueError(
                "YAGAMI_REQUIRE_AUTH is true but neither YAGAMI_API_KEYS nor OIDC is configured"
            )
        self._keys = keys
        self._oidc_issuer = settings.oidc_issuer.rstrip("/")
        self._oidc_audience = settings.oidc_audience or None
        self._oidc_project_claim = settings.oidc_project_claim
        self._oidc_roles_claim = settings.oidc_roles_claim
        self._oidc_scopes_claim = settings.oidc_scopes_claim
        self._jwks = (
            jwt.PyJWKClient(settings.oidc_jwks_url, cache_jwk_set=True, lifespan=300)
            if settings.oidc_issuer
            else None
        )
        self.required = settings.require_auth or bool(keys) or bool(settings.oidc_issuer)

    @staticmethod
    def _string_set(value: object, *, split_spaces: bool = False) -> frozenset[str]:
        if isinstance(value, str):
            return frozenset(value.split() if split_spaces else [value])
        if isinstance(value, list):
            return frozenset(item for item in value if isinstance(item, str) and item)
        return frozenset()

    def _authenticate_oidc(self, token: str) -> Principal | None:
        if self._jwks is None:
            return None
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"],
                audience=self._oidc_audience,
                issuer=self._oidc_issuer,
                options={"verify_aud": self._oidc_audience is not None, "require": ["exp", "sub"]},
            )
        except jwt.PyJWTError:
            return None
        project = claims.get(self._oidc_project_claim)
        if not isinstance(project, str) or not _PROJECT_ID.fullmatch(project):
            return None
        subject = claims.get("sub")
        roles = self._string_set(claims.get(self._oidc_roles_claim))
        scopes = self._string_set(claims.get(self._oidc_scopes_claim), split_spaces=True)
        return Principal(
            project_id=project,
            key_fingerprint=_fingerprint(token),
            authenticated=True,
            subject_id=str(subject),
            roles=roles,
            scopes=scopes,
        )

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
                    subject_id=f"api-key:{_fingerprint(record.key)}",
                    roles=record.roles,
                    scopes=record.scopes,
                )
        oidc_principal = self._authenticate_oidc(token)
        if oidc_principal is not None:
            return oidc_principal
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
    return await asyncio.to_thread(authenticator.authenticate, token)


def _is_loopback_client(request: Request) -> bool:
    host = request.client.host if request.client is not None else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> Principal:
    """Allow the local desktop UI or an explicitly privileged service key.

    The interactive UI intentionally has no browser credential flow. Loopback
    clients therefore retain the local single-user experience, while any
    non-loopback caller must present a key carrying the ``local-admin`` role or
    the ``admin:*``/``admin:access`` scope.
    """
    if _is_loopback_client(request):
        return Principal(project_id="local", key_fingerprint=None, authenticated=False)
    authenticator: Authenticator = request.app.state.runtime.authenticator
    token = credentials.credentials if credentials is not None else None
    principal = await asyncio.to_thread(authenticator.authenticate, token)
    if not (
        "local-admin" in principal.roles
        or "admin:access" in principal.scopes
        or "admin:*" in principal.scopes
    ):
        raise HTTPException(status_code=403, detail="administrator privileges required")
    return principal


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
    "require_admin",
    "require_principal",
    "require_scope",
]
