from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from yagami.config import McpServerConfig
from yagami.skills.mcp_auth import OAuthClientCredentialsAuth, validate_remote_url


def test_remote_mcp_urls_require_https_except_loopback() -> None:
    assert validate_remote_url("https://tools.example/mcp", field="url")
    assert validate_remote_url("http://127.0.0.1:9000/mcp", field="url")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_remote_url("http://tools.example/mcp", field="url")
    with pytest.raises(ValueError, match="without credentials"):
        validate_remote_url("https://user:pass@tools.example/mcp", field="url")


def test_remote_mcp_config_requires_dedicated_oauth_fields() -> None:
    with pytest.raises(ValueError, match="missing"):
        McpServerConfig(
            transport="streamable_http",
            url="https://tools.example/mcp",
            auth="client_credentials",
        )
    configured = McpServerConfig(
        transport="streamable_http",
        url="https://tools.example/mcp",
        auth="client_credentials",
        oauth_token_url="https://identity.example/token",
        oauth_client_id_env="MCP_CLIENT_ID",
        oauth_client_secret_env="MCP_CLIENT_SECRET",
        oauth_resource="https://tools.example/mcp",
    )
    assert configured.oauth_resource.endswith("/mcp")


@pytest.mark.asyncio
async def test_client_credentials_uses_resource_and_caches_token() -> None:
    token_requests: list[httpx.Request] = []
    tool_requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "identity.example":
            token_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "access_token": "dedicated-mcp-token",
                    "token_type": "Bearer",
                    "expires_in": 600,
                },
            )
        tool_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    auth = OAuthClientCredentialsAuth(
        token_url="https://identity.example/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["tools.read"],
        resource="https://tools.example/mcp",
        transport=transport,
    )
    async with httpx.AsyncClient(transport=transport, auth=auth) as client:
        await client.get("https://tools.example/one")
        await client.get("https://tools.example/two")
    await auth.aclose()

    assert len(token_requests) == 1
    form = parse_qs(token_requests[0].content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["resource"] == ["https://tools.example/mcp"]
    assert form["scope"] == ["tools.read"]
    assert len(tool_requests) == 2
    assert all(
        request.headers["Authorization"] == "Bearer dedicated-mcp-token"
        for request in tool_requests
    )
