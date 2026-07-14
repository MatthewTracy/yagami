from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from yagami import cli
from yagami.policy.bundle import build_bundle, generate_keypair, verify_bundle
from yagami.policy.testing import run_suite


def _project_file(name: str) -> Path:
    return Path(__file__).parents[1] / "config" / name


def test_default_policy_suite_passes():
    results = run_suite(_project_file("policy.yaml"), _project_file("policy-tests.yaml"))

    assert len(results) == 3
    assert all(result.passed for result in results)


def test_policy_cli_reports_a_failed_expectation(tmp_path, capsys):
    suite = tmp_path / "policy-tests.yaml"
    suite.write_text(
        """
version: 1
cases:
  - name: deliberately wrong
    input:
      context: {project_id: default}
      detected_sensitivity: none
    expect: {route: deny}
""".lstrip(),
        encoding="utf-8",
    )

    result = cli.main(
        [
            "policy",
            "test",
            "--policy",
            str(_project_file("policy.yaml")),
            "--cases",
            str(suite),
        ]
    )

    assert result == 1
    assert "[FAIL] deliberately wrong" in capsys.readouterr().out


def test_signed_policy_bundle_round_trip_is_deterministic(tmp_path):
    private_key = tmp_path / "signing.policy-private.pem"
    public_key = tmp_path / "signing-public.pem"
    first = tmp_path / "first.policy-bundle.zip"
    second = tmp_path / "second.policy-bundle.zip"
    generate_keypair(private_key, public_key)

    first_manifest = build_bundle(_project_file("policy.yaml"), private_key, first)
    second_manifest = build_bundle(_project_file("policy.yaml"), private_key, second)

    assert first.read_bytes() == second.read_bytes()
    assert verify_bundle(first, public_key) == first_manifest == second_manifest
    assert first_manifest["format"] == "yagami-policy-bundle/v1"


def test_policy_bundle_rejects_tampered_content(tmp_path):
    private_key = tmp_path / "signing.pem"
    public_key = tmp_path / "public.pem"
    bundle = tmp_path / "policy.zip"
    tampered = tmp_path / "tampered.zip"
    generate_keypair(private_key, public_key)
    build_bundle(_project_file("policy.yaml"), private_key, bundle)

    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            value = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(value)
                manifest["policy"]["id"] = "tampered"
                value = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            target.writestr(name, value)

    with pytest.raises(ValueError, match="signature is invalid"):
        verify_bundle(tampered, public_key)


def test_policy_keygen_refuses_to_replace_keys(tmp_path):
    private_key = tmp_path / "signing.pem"
    public_key = tmp_path / "public.pem"
    generate_keypair(private_key, public_key)

    with pytest.raises(FileExistsError):
        generate_keypair(private_key, public_key)
