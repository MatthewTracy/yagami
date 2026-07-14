from __future__ import annotations

import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from yagami.auth import Authenticator
from yagami.config import Settings


def test_auth_disabled_defaults_to_local_project() -> None:
    principal = Authenticator(Settings(api_keys="", require_auth=False)).authenticate(None)
    assert principal.project_id == "local"
    assert principal.authenticated is False


def test_auth_maps_bearer_key_to_project() -> None:
    auth = Authenticator(Settings(api_keys="alpha:0123456789abcdef", require_auth=True))
    principal = auth.authenticate("0123456789abcdef")
    assert principal.project_id == "alpha"
    assert principal.authenticated is True
    assert principal.key_fingerprint


def test_auth_rejects_missing_and_invalid_keys() -> None:
    auth = Authenticator(Settings(api_keys='{"alpha":"0123456789abcdef"}'))
    with pytest.raises(HTTPException) as missing:
        auth.authenticate(None)
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as invalid:
        auth.authenticate("not-the-right-key")
    assert invalid.value.status_code == 401


def test_required_auth_must_have_keys() -> None:
    with pytest.raises(ValueError, match="API_KEYS"):
        Authenticator(Settings(api_keys="", require_auth=True))


def test_multiple_separation_of_duties_keys_can_share_a_project() -> None:
    gateway_key = "gateway-key-0123456789"
    approver_key = "approver-key-0123456789"
    auth = Authenticator(
        Settings(
            api_keys=json.dumps(
                {
                    "alpha": [
                        gateway_key,
                        {
                            "key": approver_key,
                            "roles": ["security-approver"],
                            "scopes": ["tools:approve"],
                        },
                    ]
                }
            )
        )
    )
    gateway = auth.authenticate(gateway_key)
    approver = auth.authenticate(approver_key)
    assert gateway.project_id == approver.project_id == "alpha"
    assert "gateway:invoke" in gateway.scopes
    assert approver.scopes == frozenset({"tools:approve"})


def test_oidc_jwt_maps_workload_identity_to_project_roles_and_scopes() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    auth = Authenticator(
        Settings(
            require_auth=True,
            oidc_issuer="https://identity.example",
            oidc_audience="yagami-gateway",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        )
    )
    auth._jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public_key)
    )
    token = jwt.encode(
        {
            "iss": "https://identity.example",
            "aud": "yagami-gateway",
            "sub": "workload:claims-processor",
            "exp": int(time.time()) + 300,
            "yagami_project": "claims",
            "roles": ["service"],
            "scope": "gateway:invoke policy:preview",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    principal = auth.authenticate(token)

    assert principal.project_id == "claims"
    assert principal.subject_id == "workload:claims-processor"
    assert principal.roles == frozenset({"service"})
    assert principal.scopes == frozenset({"gateway:invoke", "policy:preview"})


def test_oidc_rejects_tokens_without_project_claim() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    auth = Authenticator(
        Settings(
            oidc_issuer="https://identity.example",
            oidc_jwks_url="https://identity.example/jwks.json",
        )
    )
    auth._jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=private_key.public_key())
    )
    token = jwt.encode(
        {
            "iss": "https://identity.example",
            "sub": "workload",
            "exp": int(time.time()) + 300,
        },
        private_key,
        algorithm="RS256",
    )

    with pytest.raises(HTTPException) as invalid:
        auth.authenticate(token)
    assert invalid.value.status_code == 401
