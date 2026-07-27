"""Encrypted OAuth 2.1 authorization-code credentials for remote MCP users."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..backends.base import TrustZone
from ..config import McpServerConfig
from ..key_management import KeyWrappingProvider, WrappedDataKey
from ..storage.db import get_db, now_ms
from .mcp_auth import validate_remote_destination


class OAuthCredentialError(RuntimeError):
    """A safe OAuth failure with a stable machine-readable reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class OAuthCompletion:
    server_name: str
    project_id: str
    subject_hash: str
    access_expires_at: int


class OAuthCredentialStore:
    """Envelope-encrypted, project/subject-bound OAuth token storage."""

    def __init__(
        self,
        *,
        key_wrapper: KeyWrappingProvider,
        identity_key: bytes,
        state_ttl_seconds: int = 600,
        transport: httpx.AsyncBaseTransport | None = None,
        validate_destinations: bool = True,
    ) -> None:
        if len(identity_key) != 32:
            raise ValueError("OAuth identity key must contain exactly 32 bytes")
        self._key_wrapper = key_wrapper
        self._identity_key = identity_key
        self._state_ttl_seconds = state_ttl_seconds
        self._transport = transport
        self._validate_destinations = validate_destinations
        self._refresh_locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    def subject_hash(self, project_id: str, subject_id: str) -> str:
        return hmac.new(
            self._identity_key,
            f"{project_id}\0{subject_id}".encode(),
            hashlib.sha256,
        ).hexdigest()

    async def begin(
        self,
        *,
        server_name: str,
        config: McpServerConfig,
        project_id: str,
        subject_id: str,
    ) -> dict[str, Any]:
        self._require_authorization_code(config)
        client_id = self._client_id(config)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
        challenge = challenge.rstrip("=")
        subject_hash = self.subject_hash(project_id, subject_id)
        created_at = now_ms()
        expires_at = created_at + self._state_ttl_seconds * 1000
        aad = self._state_aad(
            server_name=server_name,
            project_id=project_id,
            subject_hash=subject_hash,
            state_hash=self._state_hash(state),
        )
        encrypted = self._encrypt(
            json.dumps({"code_verifier": verifier}, separators=(",", ":")).encode(),
            aad=aad,
        )
        db = get_db()
        await db.execute(
            "INSERT INTO mcp_oauth_states(state_hash, server_name, project_id, subject_hash,"
            " nonce, ciphertext, wrapped_key, wrapping_key_id, key_epoch, created_at,"
            " expires_at, consumed_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                self._state_hash(state),
                server_name,
                project_id,
                subject_hash,
                encrypted["nonce"],
                encrypted["ciphertext"],
                encrypted["wrapped_key"],
                encrypted["wrapping_key_id"],
                encrypted["key_epoch"],
                created_at,
                expires_at,
            ),
        )
        await db.commit()
        query: dict[str, str] = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": config.oauth_redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if config.oauth_scopes:
            query["scope"] = " ".join(config.oauth_scopes)
        if config.oauth_resource:
            query["resource"] = config.oauth_resource
        return {
            "authorization_url": config.oauth_authorization_url + "?" + urlencode(query),
            "expires_at": expires_at,
        }

    async def complete(
        self,
        *,
        state: str,
        code: str,
        configs: dict[str, McpServerConfig],
    ) -> OAuthCompletion:
        if not state or len(state) > 1024 or not code or len(code) > 8192:
            raise OAuthCredentialError("mcp_oauth_invalid_callback", "invalid OAuth callback")
        state_hash = self._state_hash(state)
        current = now_ms()
        db = get_db()
        cursor = await db.execute(
            "UPDATE mcp_oauth_states SET consumed_at=?"
            " WHERE state_hash=? AND consumed_at IS NULL AND expires_at>=?"
            " RETURNING server_name, project_id, subject_hash, nonce, ciphertext,"
            " wrapped_key, wrapping_key_id, key_epoch",
            (current, state_hash, current),
        )
        row = await cursor.fetchone()
        await db.commit()
        if row is None:
            raise OAuthCredentialError(
                "mcp_oauth_state_invalid",
                "OAuth state is invalid, expired, or already used",
            )
        server_name = str(row["server_name"])
        config = configs.get(server_name)
        if config is None:
            raise OAuthCredentialError(
                "mcp_oauth_server_unavailable",
                "the configured MCP server is unavailable",
            )
        self._require_authorization_code(config)
        project_id = str(row["project_id"])
        subject_hash = str(row["subject_hash"])
        aad = self._state_aad(
            server_name=server_name,
            project_id=project_id,
            subject_hash=subject_hash,
            state_hash=state_hash,
        )
        pending = json.loads(self._decrypt(row, aad=aad))
        verifier = pending.get("code_verifier")
        if not isinstance(verifier, str):
            raise OAuthCredentialError("mcp_oauth_state_invalid", "OAuth state is malformed")
        payload = await self._token_request(
            config,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.oauth_redirect_uri,
                "code_verifier": verifier,
            },
        )
        access_expires_at = await self._store_token_payload(
            server_name=server_name,
            project_id=project_id,
            subject_hash=subject_hash,
            payload=payload,
            previous_refresh_token=None,
        )
        await db.execute("DELETE FROM mcp_oauth_states WHERE state_hash=?", (state_hash,))
        await db.commit()
        return OAuthCompletion(
            server_name=server_name,
            project_id=project_id,
            subject_hash=subject_hash,
            access_expires_at=access_expires_at,
        )

    async def has_credential(
        self,
        *,
        server_name: str,
        project_id: str,
        subject_id: str,
    ) -> bool:
        subject_hash = self.subject_hash(project_id, subject_id)
        async with get_db().execute(
            "SELECT 1 FROM mcp_oauth_credentials"
            " WHERE server_name=? AND project_id=? AND subject_hash=?",
            (server_name, project_id, subject_hash),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def access_token(
        self,
        *,
        server_name: str,
        config: McpServerConfig,
        project_id: str,
        subject_id: str,
    ) -> str:
        subject_hash = self.subject_hash(project_id, subject_id)
        key = (server_name, project_id, subject_hash)
        lock = self._refresh_locks.setdefault(key, asyncio.Lock())
        async with lock:
            credential = await self._load_credential(
                server_name=server_name,
                project_id=project_id,
                subject_hash=subject_hash,
            )
            if int(credential["expires_at"]) > now_ms() + 30_000:
                return str(credential["access_token"])
            refresh_token = credential.get("refresh_token")
            if not isinstance(refresh_token, str) or not refresh_token:
                raise OAuthCredentialError(
                    "mcp_oauth_authorization_required",
                    "the delegated MCP authorization expired and must be renewed",
                )
            payload = await self._token_request(
                config,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            await self._store_token_payload(
                server_name=server_name,
                project_id=project_id,
                subject_hash=subject_hash,
                payload=payload,
                previous_refresh_token=refresh_token,
            )
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise OAuthCredentialError(
                    "mcp_oauth_token_invalid",
                    "the OAuth server returned an invalid access token",
                )
            return token

    async def revoke(
        self,
        *,
        server_name: str,
        project_id: str,
        subject_id: str,
    ) -> bool:
        subject_hash = self.subject_hash(project_id, subject_id)
        cursor = await get_db().execute(
            "DELETE FROM mcp_oauth_credentials"
            " WHERE server_name=? AND project_id=? AND subject_hash=?",
            (server_name, project_id, subject_hash),
        )
        await get_db().commit()
        return cursor.rowcount > 0

    async def cleanup_expired_states(self) -> int:
        cursor = await get_db().execute(
            "DELETE FROM mcp_oauth_states WHERE expires_at<?",
            (now_ms(),),
        )
        await get_db().commit()
        return max(0, cursor.rowcount)

    async def cancel(self, state: str) -> bool:
        if not state or len(state) > 1024:
            return False
        cursor = await get_db().execute(
            "DELETE FROM mcp_oauth_states WHERE state_hash=? AND consumed_at IS NULL",
            (self._state_hash(state),),
        )
        await get_db().commit()
        return cursor.rowcount > 0

    async def _load_credential(
        self,
        *,
        server_name: str,
        project_id: str,
        subject_hash: str,
    ) -> dict[str, Any]:
        async with get_db().execute(
            "SELECT nonce, ciphertext, wrapped_key, wrapping_key_id, key_epoch"
            " FROM mcp_oauth_credentials"
            " WHERE server_name=? AND project_id=? AND subject_hash=?",
            (server_name, project_id, subject_hash),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise OAuthCredentialError(
                "mcp_oauth_authorization_required",
                "the user must authorize this MCP server",
            )
        aad = self._credential_aad(
            server_name=server_name,
            project_id=project_id,
            subject_hash=subject_hash,
        )
        try:
            payload = json.loads(self._decrypt(row, aad=aad))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OAuthCredentialError(
                "mcp_oauth_credential_invalid",
                "the stored MCP authorization could not be authenticated",
            ) from exc
        if not isinstance(payload, dict):
            raise OAuthCredentialError(
                "mcp_oauth_credential_invalid",
                "the stored MCP authorization is malformed",
            )
        return payload

    async def _store_token_payload(
        self,
        *,
        server_name: str,
        project_id: str,
        subject_hash: str,
        payload: dict[str, Any],
        previous_refresh_token: str | None,
    ) -> int:
        access_token = payload.get("access_token")
        token_type = payload.get("token_type", "Bearer")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthCredentialError(
                "mcp_oauth_token_invalid",
                "the OAuth server omitted the access token",
            )
        if not isinstance(token_type, str) or token_type.casefold() != "bearer":
            raise OAuthCredentialError(
                "mcp_oauth_token_type_unsupported",
                "the OAuth server returned an unsupported token type",
            )
        try:
            expires_in = max(1, int(payload.get("expires_in", 300)))
        except (TypeError, ValueError):
            expires_in = 300
        current = now_ms()
        expires_at = current + expires_in * 1000
        refresh_token = payload.get("refresh_token", previous_refresh_token)
        credential = {
            "access_token": access_token,
            "refresh_token": refresh_token if isinstance(refresh_token, str) else None,
            "expires_at": expires_at,
        }
        aad = self._credential_aad(
            server_name=server_name,
            project_id=project_id,
            subject_hash=subject_hash,
        )
        encrypted = self._encrypt(
            json.dumps(credential, separators=(",", ":")).encode(),
            aad=aad,
        )
        await get_db().execute(
            "INSERT INTO mcp_oauth_credentials(server_name, project_id, subject_hash,"
            " nonce, ciphertext, wrapped_key, wrapping_key_id, key_epoch,"
            " access_expires_at, created_at, updated_at, last_used_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(server_name, project_id, subject_hash) DO UPDATE SET"
            " nonce=excluded.nonce, ciphertext=excluded.ciphertext,"
            " wrapped_key=excluded.wrapped_key, wrapping_key_id=excluded.wrapping_key_id,"
            " key_epoch=excluded.key_epoch, access_expires_at=excluded.access_expires_at,"
            " updated_at=excluded.updated_at, last_used_at=excluded.last_used_at",
            (
                server_name,
                project_id,
                subject_hash,
                encrypted["nonce"],
                encrypted["ciphertext"],
                encrypted["wrapped_key"],
                encrypted["wrapping_key_id"],
                encrypted["key_epoch"],
                expires_at,
                current,
                current,
                current,
            ),
        )
        await get_db().commit()
        return expires_at

    async def _token_request(
        self,
        config: McpServerConfig,
        grant: dict[str, str],
    ) -> dict[str, Any]:
        self._require_authorization_code(config)
        token_url = config.oauth_token_url
        if self._validate_destinations:
            token_url = await validate_remote_destination(
                token_url,
                field="oauth_token_url",
                trust_zone=TrustZone.EXTERNAL,
            )
        data = {
            **grant,
            "client_id": self._client_id(config),
        }
        if config.oauth_scopes:
            data["scope"] = " ".join(config.oauth_scopes)
        if config.oauth_resource:
            data["resource"] = config.oauth_resource
        auth: httpx.Auth | None = None
        client_secret = (
            os.getenv(config.oauth_client_secret_env, "") if config.oauth_client_secret_env else ""
        )
        if config.oauth_token_endpoint_auth_method == "client_secret_basic":  # noqa: S105
            if not client_secret:
                raise OAuthCredentialError(
                    "mcp_oauth_configuration_invalid",
                    "the MCP OAuth client secret is unavailable",
                )
            auth = httpx.BasicAuth(data.pop("client_id"), client_secret)
        elif config.oauth_token_endpoint_auth_method == "client_secret_post":  # noqa: S105
            if not client_secret:
                raise OAuthCredentialError(
                    "mcp_oauth_configuration_invalid",
                    "the MCP OAuth client secret is unavailable",
                )
            data["client_secret"] = client_secret
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = (
                    await client.post(token_url, data=data)
                    if auth is None
                    else await client.post(token_url, data=data, auth=auth)
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthCredentialError(
                "mcp_oauth_exchange_failed",
                "the OAuth server did not complete the token exchange",
            ) from exc
        if not isinstance(payload, dict):
            raise OAuthCredentialError(
                "mcp_oauth_token_invalid",
                "the OAuth server returned an invalid token response",
            )
        return payload

    def _client_id(self, config: McpServerConfig) -> str:
        client_id = os.getenv(config.oauth_client_id_env, "")
        if not client_id:
            raise OAuthCredentialError(
                "mcp_oauth_configuration_invalid",
                "the MCP OAuth client ID is unavailable",
            )
        return client_id

    @staticmethod
    def _require_authorization_code(config: McpServerConfig) -> None:
        if config.auth != "authorization_code_pkce":
            raise OAuthCredentialError(
                "mcp_oauth_not_configured",
                "the MCP server does not use user-bound OAuth",
            )

    @staticmethod
    def _state_hash(state: str) -> str:
        return hashlib.sha256(state.encode()).hexdigest()

    @staticmethod
    def _state_aad(
        *,
        server_name: str,
        project_id: str,
        subject_hash: str,
        state_hash: str,
    ) -> bytes:
        return f"yagami-mcp-oauth-state-v1:{server_name}:{project_id}:{subject_hash}:{state_hash}".encode()

    @staticmethod
    def _credential_aad(
        *,
        server_name: str,
        project_id: str,
        subject_hash: str,
    ) -> bytes:
        return f"yagami-mcp-oauth-token-v1:{server_name}:{project_id}:{subject_hash}".encode()

    def _encrypt(self, plaintext: bytes, *, aad: bytes) -> dict[str, Any]:
        data_key = os.urandom(32)
        nonce = os.urandom(12)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)
        wrapped = self._key_wrapper.wrap(data_key, context=aad)
        return {
            "nonce": nonce,
            "ciphertext": ciphertext,
            "wrapped_key": wrapped.ciphertext,
            "wrapping_key_id": wrapped.key_id,
            "key_epoch": wrapped.key_epoch,
        }

    def _decrypt(self, row: Any, *, aad: bytes) -> str:
        wrapped = WrappedDataKey(
            ciphertext=bytes(row["wrapped_key"]),
            key_id=str(row["wrapping_key_id"]),
            key_epoch=int(row["key_epoch"]),
        )
        try:
            data_key = self._key_wrapper.unwrap(wrapped, context=aad)
            plaintext = AESGCM(data_key).decrypt(
                bytes(row["nonce"]),
                bytes(row["ciphertext"]),
                aad,
            )
            return plaintext.decode()
        except Exception as exc:  # noqa: BLE001 - authentication has one safe outcome
            raise OAuthCredentialError(
                "mcp_oauth_credential_invalid",
                "the stored MCP authorization could not be authenticated",
            ) from exc


class OAuthUserAuth(httpx.Auth):
    """Inject one project's delegated user token into an MCP HTTP session."""

    def __init__(
        self,
        *,
        store: OAuthCredentialStore,
        server_name: str,
        config: McpServerConfig,
        project_id: str,
        subject_id: str,
    ) -> None:
        self._store = store
        self._server_name = server_name
        self._config = config
        self._project_id = project_id
        self._subject_id = subject_id

    async def async_auth_flow(self, request: httpx.Request):
        token = await self._store.access_token(
            server_name=self._server_name,
            config=self._config,
            project_id=self._project_id,
            subject_id=self._subject_id,
        )
        request.headers["Authorization"] = "Bearer " + token
        yield request
