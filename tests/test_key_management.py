from __future__ import annotations

from types import SimpleNamespace

import pytest

from yagami import key_management


def test_resolves_environment_and_file_secret_references(tmp_path, monkeypatch):
    monkeypatch.setenv("YAGAMI_TEST_SECRET", "environment-value")
    secret_file = tmp_path / "secret"
    secret_file.write_text("file-value\n", encoding="utf-8")

    assert (
        key_management.resolve_secret_reference("env:YAGAMI_TEST_SECRET", label="test secret")
        == "environment-value"
    )
    assert (
        key_management.resolve_secret_reference(f"file:{secret_file}", label="test secret")
        == "file-value"
    )


def test_resolves_os_keyring_reference(monkeypatch):
    monkeypatch.setattr(
        key_management,
        "keyring",
        SimpleNamespace(get_password=lambda service, account: f"{service}:{account}"),
    )

    assert (
        key_management.resolve_secret_reference("keyring:yagami/audit", label="audit key")
        == "yagami:audit"
    )


@pytest.mark.parametrize(
    "reference",
    ["literal-secret", "unknown:value", "env:INVALID-NAME", "keyring:missing-account"],
)
def test_rejects_malformed_or_unsupported_secret_references(reference):
    with pytest.raises(ValueError):
        key_management.resolve_secret_reference(reference, label="test secret")


def test_reference_takes_precedence_over_legacy_direct_value(monkeypatch):
    monkeypatch.setenv("YAGAMI_TEST_SECRET", "referenced")

    assert (
        key_management.resolve_secret("legacy", "env:YAGAMI_TEST_SECRET", label="test secret")
        == "referenced"
    )
