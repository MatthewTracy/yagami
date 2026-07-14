from __future__ import annotations

import json

import pytest
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
