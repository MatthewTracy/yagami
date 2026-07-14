"""Deterministic, Ed25519-signed policy bundles."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .engine import PolicyEngine

_BUNDLE_FORMAT = "yagami-policy-bundle/v1"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _write_zip_entry(archive: zipfile.ZipFile, name: str, value: bytes) -> None:
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, value)


def generate_keypair(private_path: Path, public_path: Path, *, force: bool = False) -> None:
    if not force and (private_path.exists() or public_path.exists()):
        raise FileExistsError("refusing to replace an existing policy signing key; use --force")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path.write_bytes(private_bytes)
    public_path.write_bytes(public_bytes)
    if os.name != "nt":
        private_path.chmod(0o600)
        public_path.chmod(0o644)


def build_bundle(policy_path: Path, private_key_path: Path, output_path: Path) -> dict[str, Any]:
    policy_bytes = policy_path.read_bytes()
    engine = PolicyEngine(policy_path)
    document = engine.document
    private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("policy signing key must be an Ed25519 private key")
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    manifest: dict[str, Any] = {
        "format": _BUNDLE_FORMAT,
        "policy": {
            "id": document.id,
            "version": document.version,
            "canonical_hash": engine.policy_hash,
            "source_sha256": _sha256(policy_bytes),
        },
        "signing_key_sha256": _sha256(public_bytes),
    }
    manifest_bytes = _canonical_json(manifest)
    signature = private_key.sign(manifest_bytes)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w") as archive:
        _write_zip_entry(archive, "manifest.json", manifest_bytes)
        _write_zip_entry(archive, "policy.yaml", policy_bytes)
        _write_zip_entry(archive, "signature.ed25519", signature)
    return manifest


def verify_bundle(bundle_path: Path, public_key_path: Path) -> dict[str, Any]:
    public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("policy verification key must be an Ed25519 public key")
    with zipfile.ZipFile(bundle_path, "r") as archive:
        names = set(archive.namelist())
        required = {"manifest.json", "policy.yaml", "signature.ed25519"}
        if names != required:
            raise ValueError(f"invalid policy bundle members: expected {sorted(required)!r}")
        manifest_bytes = archive.read("manifest.json")
        policy_bytes = archive.read("policy.yaml")
        signature = archive.read("signature.ed25519")
    try:
        public_key.verify(signature, manifest_bytes)
    except InvalidSignature as exc:
        raise ValueError("policy bundle signature is invalid") from exc
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("format") != _BUNDLE_FORMAT:
        raise ValueError("unsupported policy bundle format")
    policy = manifest.get("policy")
    if not isinstance(policy, dict) or policy.get("source_sha256") != _sha256(policy_bytes):
        raise ValueError("policy bundle content digest does not match its manifest")
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if manifest.get("signing_key_sha256") != _sha256(public_bytes):
        raise ValueError("policy bundle was signed by a different key")
    return manifest
