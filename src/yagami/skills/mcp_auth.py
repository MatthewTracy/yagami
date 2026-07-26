from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import socket
import time
from urllib.parse import urlsplit

import httpx

from ..backends.base import TrustZone


def _address_allowed(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    trust_zone: TrustZone,
    allow_private_addresses: bool,
) -> bool:
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        return False
    if trust_zone == TrustZone.DEVICE:
        return address.is_loopback
    if trust_zone == TrustZone.PRIVATE_NETWORK:
        return address.is_loopback or address.is_private
    if allow_private_addresses:
        return True
    return bool(address.is_global)


def _host_allowed(host: str, allowed_hosts: list[str]) -> bool:
    return not allowed_hosts or any(
        fnmatch.fnmatchcase(host, pattern) for pattern in allowed_hosts
    )


def validate_remote_url(
    url: str,
    *,
    field: str,
    trust_zone: TrustZone | None = None,
    allowed_hosts: list[str] | None = None,
    allow_private_addresses: bool = False,
) -> str:
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
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{field} must be an absolute HTTP(S) URL without credentials, query, or fragment"
        )
    host = parsed.hostname.rstrip(".").casefold()
    if trust_zone is None:
        try:
            loopback_literal = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback_literal = False
        trust_zone = (
            TrustZone.DEVICE if host == "localhost" or loopback_literal else TrustZone.EXTERNAL
        )
    if not _host_allowed(host, allowed_hosts or []):
        raise ValueError(f"{field} host is not in allowed_hosts")
    if trust_zone in {TrustZone.APPROVED_CLOUD, TrustZone.EXTERNAL} and parsed.scheme != "https":
        raise ValueError(f"{field} must use HTTPS outside a private trust zone")
    if parsed.scheme == "http":
        is_private = host in {"localhost", "host.docker.internal", "gateway.docker.internal"}
        try:
            address = ipaddress.ip_address(host)
            is_private = is_private or _address_allowed(
                address,
                trust_zone=trust_zone,
                allow_private_addresses=allow_private_addresses,
            )
        except ValueError:
            pass
        if not is_private:
            raise ValueError(f"{field} must use HTTPS unless it targets an allowed private host")
    if port == 0:
        raise ValueError(f"{field} requires a valid nonzero port")
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        literal_address = None
    if literal_address is not None and not _address_allowed(
        literal_address,
        trust_zone=trust_zone,
        allow_private_addresses=allow_private_addresses,
    ):
        raise ValueError(f"{field} resolves to an address outside its trust zone")
    return url


async def validate_remote_destination(
    url: str,
    *,
    field: str,
    trust_zone: TrustZone | None = None,
    allowed_hosts: list[str] | None = None,
    allow_private_addresses: bool = False,
) -> str:
    """Resolve every destination before use and reject unsafe address classes.

    This check runs before each connection and HTTP request. Redirects remain
    disabled, preventing a validated endpoint from redirecting the client to a
    different network boundary.
    """

    validated = validate_remote_url(
        url,
        field=field,
        trust_zone=trust_zone,
        allowed_hosts=allowed_hosts,
        allow_private_addresses=allow_private_addresses,
    )
    parsed = urlsplit(validated)
    host = parsed.hostname
    if host is None:  # pragma: no cover - guarded by validate_remote_url
        raise ValueError(f"{field} must include a host")
    if trust_zone is None:
        try:
            loopback_literal = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback_literal = False
        trust_zone = (
            TrustZone.DEVICE if host == "localhost" or loopback_literal else TrustZone.EXTERNAL
        )
    try:
        literal = ipaddress.ip_address(host)
        addresses = {literal}
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            records = await loop.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ValueError(f"{field} host could not be resolved") from exc
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    if not addresses or any(
        not _address_allowed(
            address,
            trust_zone=trust_zone,
            allow_private_addresses=allow_private_addresses,
        )
        for address in addresses
    ):
        raise ValueError(f"{field} resolved outside its configured trust zone")
    return validated


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
        token_endpoint_auth_method: str = "client_secret_basic",  # noqa: S107 - OAuth method
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.token_url = validate_remote_url(
            token_url, field="oauth_token_url", trust_zone=TrustZone.EXTERNAL
        )
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
            auth: httpx.Auth | None = None
            if self.token_endpoint_auth_method == "client_secret_basic":  # noqa: S105
                auth = httpx.BasicAuth(self.client_id, self.client_secret)
            else:
                data["client_id"] = self.client_id
                data["client_secret"] = self.client_secret
            if auth is None:
                response = await self._client.post(self.token_url, data=data)
            else:
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
