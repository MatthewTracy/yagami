from __future__ import annotations

import base64
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from yagami.config import McpServerConfig
from yagami.governance import generate_transform_key
from yagami.key_management import LocalAesKeyWrapper
from yagami.privacy import stream_export
from yagami.skills.base import SkillContext
from yagami.skills.mcp_manager import McpManager
from yagami.skills.mcp_oauth import OAuthCredentialError, OAuthCredentialStore
from yagami.storage.db import close_db, get_db, open_db


def _config() -> McpServerConfig:
    return McpServerConfig(
        transport="streamable_http",
        url="https://tools.example/mcp",
        auth="authorization_code_pkce",
        oauth_authorization_url="https://identity.example/authorize",
        oauth_token_url="https://identity.example/token",
        oauth_redirect_uri="https://gateway.example/v1/mcp/oauth/callback",
        oauth_client_id_env="MCP_USER_CLIENT_ID",
        oauth_scopes=["tools.read", "tools.execute"],
        oauth_resource="https://tools.example/mcp",
        oauth_token_endpoint_auth_method="none",
    )


@pytest.mark.asyncio
async def test_pkce_state_tokens_refresh_and_revoke_are_bound_and_encrypted(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_USER_CLIENT_ID", "public-client-id")
    token_requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        token_requests.append(request)
        form = parse_qs(request.content.decode())
        if form["grant_type"] == ["authorization_code"]:
            return httpx.Response(
                200,
                json={
                    "access_token": "first-delegated-token",
                    "refresh_token": "delegated-refresh-token",
                    "token_type": "Bearer",
                    "expires_in": 1,
                },
            )
        return httpx.Response(
            200,
            json={
                "access_token": "refreshed-delegated-token",
                "token_type": "Bearer",
                "expires_in": 600,
            },
        )

    key = generate_transform_key()
    store = OAuthCredentialStore(
        key_wrapper=LocalAesKeyWrapper(key=key, key_id="oauth-test", key_epoch=1),
        identity_key=base64.urlsafe_b64decode(key),
        transport=httpx.MockTransport(handler),
        validate_destinations=False,
    )
    await open_db(tmp_path / "oauth.db")
    try:
        authorization = await store.begin(
            server_name="work-tools",
            config=_config(),
            project_id="engineering",
            subject_id="user-123",
        )
        query = parse_qs(urlsplit(authorization["authorization_url"]).query)
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["resource"] == ["https://tools.example/mcp"]
        assert query["scope"] == ["tools.read tools.execute"]
        assert "code_verifier" not in query
        state = query["state"][0]

        completion = await store.complete(
            state=state,
            code="authorization-code",
            configs={"work-tools": _config()},
        )
        assert completion.project_id == "engineering"
        assert completion.subject_hash != "user-123"
        assert await store.has_credential(
            server_name="work-tools",
            project_id="engineering",
            subject_id="user-123",
        )
        with pytest.raises(OAuthCredentialError, match="already used"):
            await store.complete(
                state=state,
                code="authorization-code-replay",
                configs={"work-tools": _config()},
            )

        token = await store.access_token(
            server_name="work-tools",
            config=_config(),
            project_id="engineering",
            subject_id="user-123",
        )
        assert token == "refreshed-delegated-token"  # noqa: S105 - mock OAuth token
        assert len(token_requests) == 2
        refresh_form = parse_qs(token_requests[1].content.decode())
        assert refresh_form["grant_type"] == ["refresh_token"]
        assert refresh_form["refresh_token"] == ["delegated-refresh-token"]

        with pytest.raises(OAuthCredentialError, match="must authorize"):
            await store.access_token(
                server_name="work-tools",
                config=_config(),
                project_id="engineering",
                subject_id="different-user",
            )

        async with get_db().execute(
            "SELECT server_name, subject_hash, ciphertext FROM mcp_oauth_credentials"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        serialized = b"|".join(
            (
                str(row["server_name"]).encode(),
                str(row["subject_hash"]).encode(),
                bytes(row["ciphertext"]),
            )
        )
        assert b"user-123" not in serialized
        assert b"delegated-token" not in serialized
        assert b"delegated-refresh-token" not in serialized
        exported = "".join([chunk async for chunk in stream_export()])
        assert "mcp_oauth_credentials" not in exported
        assert "delegated-token" not in exported

        assert await store.revoke(
            server_name="work-tools",
            project_id="engineering",
            subject_id="user-123",
        )
        assert not await store.has_credential(
            server_name="work-tools",
            project_id="engineering",
            subject_id="user-123",
        )
    finally:
        await close_db()


def test_authorization_code_config_requires_pkce_endpoints_and_public_client_mode() -> None:
    with pytest.raises(ValueError, match="missing"):
        McpServerConfig(
            transport="streamable_http",
            url="https://tools.example/mcp",
            auth="authorization_code_pkce",
        )
    with pytest.raises(ValueError, match="oauth_client_secret_env"):
        McpServerConfig(
            transport="streamable_http",
            url="https://tools.example/mcp",
            auth="authorization_code_pkce",
            oauth_authorization_url="https://identity.example/authorize",
            oauth_token_url="https://identity.example/token",
            oauth_redirect_uri="https://gateway.example/v1/mcp/oauth/callback",
            oauth_client_id_env="MCP_USER_CLIENT_ID",
            oauth_resource="https://tools.example/mcp",
        )


@pytest.mark.asyncio
async def test_mcp_manager_executes_only_through_the_authorized_subject_session(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_USER_CLIENT_ID", "public-client-id")

    async def token_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "subject-bound-token",
                "token_type": "Bearer",
                "expires_in": 600,
            },
        )

    key = generate_transform_key()
    store = OAuthCredentialStore(
        key_wrapper=LocalAesKeyWrapper(key=key, key_id="oauth-test", key_epoch=1),
        identity_key=base64.urlsafe_b64decode(key),
        transport=httpx.MockTransport(token_handler),
        validate_destinations=False,
    )

    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="deploy",
                        description="Deploy a reviewed artifact",
                        inputSchema={
                            "type": "object",
                            "properties": {"version": {"type": "string"}},
                            "required": ["version"],
                        },
                    )
                ]
            )

        async def list_resources(self):
            return SimpleNamespace(resources=[])

        async def list_prompts(self):
            return SimpleNamespace(prompts=[])

        async def call_tool(self, _name, _args, *, read_timeout_seconds):
            assert read_timeout_seconds is not None
            return SimpleNamespace(
                content=[SimpleNamespace(text="deployed")],
                structuredContent=None,
                isError=False,
            )

    await open_db(tmp_path / "manager-oauth.db")
    manager = McpManager(oauth_credentials=store)
    config = _config()
    await manager.connect_all({"work-tools": config})
    try:
        authorization = await store.begin(
            server_name="work-tools",
            config=config,
            project_id="engineering",
            subject_id="user-123",
        )
        state = parse_qs(urlsplit(authorization["authorization_url"]).query)["state"][0]
        await store.complete(
            state=state,
            code="authorization-code",
            configs={"work-tools": config},
        )

        async def fake_open_http(_stack, _name, _config, *, oauth_subject=None):
            assert oauth_subject == ("engineering", "user-123")
            return FakeSession()

        monkeypatch.setattr(manager, "_open_http", fake_open_http)
        assert (
            await manager.connect_for_subject(
                "work-tools",
                project_id="engineering",
                subject_id="user-123",
            )
            == 1
        )
        result = await manager.call_tool(
            "mcp.work-tools.deploy",
            {"version": "1.2.3"},
            SkillContext(
                session_id="session",
                project_id="engineering",
                subject_id="user-123",
            ),
        )
        assert result.ok
        assert result.content == "deployed"
        assert (
            len(
                manager.catalog_for_subject(
                    project_id="engineering",
                    subject_id="user-123",
                )["tools"]
            )
            == 1
        )
        assert (
            manager.catalog_for_subject(
                project_id="engineering",
                subject_id="different-user",
            )["tools"]
            == []
        )

        denied = await manager.call_tool(
            "mcp.work-tools.deploy",
            {"version": "1.2.3"},
            SkillContext(
                session_id="session",
                project_id="engineering",
                subject_id="different-user",
            ),
        )
        assert not denied.ok
        assert denied.artifacts["error_code"] == "mcp_oauth_authorization_required"
    finally:
        await manager.close_all()
        await close_db()
