from __future__ import annotations

import asyncio
import ipaddress
import time
from urllib.parse import urlsplit

import httpx


def validate_remote_url(url: str, *, field: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{field} must be an absolute HTTP(S) URL without credentials")
    if parsed.scheme == "http":
        host = parsed.hostname.casefold()
        is_loopback = host == "localhost"
        try:
            is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if not is_loopback:
            raise ValueError(f"{field} must use HTTPS unless it targets loopback")
    del port
    return url


class OAuthClientCredentialsAuth(httpx.Auth):
    """Dedicated MCP client-credentials token source with refresh caching."""

    requires_response_body = True

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        resource: str,
        token_endpoint_auth_method: str = "client_secret_basic",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.token_url = validate_remote_url(token_url, field="oauth_token_url")
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.resource = resource
        self.token_endpoint_auth_method = token_endpoint_auth_method
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=False,
            transport=transport,
        )
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _access_token(self) -> str:
        now = time.monotonic()
        if self._token is not None and now < self._expires_at - 30:
            return self._token
        async with self._lock:
            now = time.monotonic()
            if self._token is not None and now < self._expires_at - 30:
                return self._token
            data = {
                "grant_type": "client_credentials",
                "resource": self.resource,
            }
            if self.scopes:
                data["scope"] = " ".join(self.scopes)
            auth = None
            if self.token_endpoint_auth_method == "client_secret_basic":
                auth = httpx.BasicAuth(self.client_id, self.client_secret)
            else:
                data["client_id"] = self.client_id
                data["client_secret"] = self.client_secret
            response = await self._client.post(self.token_url, data=data, auth=auth)
            response.raise_for_status()
            payload = response.json()
            token = payload.get("access_token") if isinstance(payload, dict) else None
            token_type = payload.get("token_type", "Bearer") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                raise httpx.HTTPError("OAuth token response omitted access_token")
            if not isinstance(token_type, str) or token_type.casefold() != "bearer":
                raise httpx.HTTPError("OAuth token response used unsupported token_type")
            try:
                expires_in = max(60, int(payload.get("expires_in", 300)))
            except (TypeError, ValueError):
                expires_in = 300
            self._token = token
            self._expires_at = time.monotonic() + expires_in
            return token

    async def async_auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = "Bearer " + await self._access_token()
        yield request

    async def aclose(self) -> None:
        self._token = None
        self._expires_at = 0
        await self._client.aclose()
