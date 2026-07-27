"""Secret resolution and envelope-key primitives.

The wrapping interface intentionally has no cloud SDK dependency. A deployment
can use the local AES key wrapper or provide the same small interface from a
KMS, Vault Transit, or HSM adapter without changing the token-vault format.
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import keyring
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_SECRET_BYTES = 64 * 1024


class KeyWrappingError(RuntimeError):
    """Raised when a data-encryption key cannot be wrapped or unwrapped."""


@dataclass(frozen=True)
class WrappedDataKey:
    """Portable envelope metadata stored alongside encrypted application data."""

    ciphertext: bytes
    key_id: str
    key_epoch: int


class KeyWrappingProvider(Protocol):
    """Minimal boundary implemented by local and external KMS key wrappers."""

    @property
    def key_id(self) -> str: ...

    @property
    def key_epoch(self) -> int: ...

    def wrap(self, plaintext_key: bytes, *, context: bytes) -> WrappedDataKey: ...

    def unwrap(self, wrapped_key: WrappedDataKey, *, context: bytes) -> bytes: ...


def parse_aes256_key(encoded: str, *, label: str) -> bytes:
    """Parse a URL-safe base64 encoded 256-bit key without logging its value."""
    try:
        value = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise KeyWrappingError(f"{label} must be URL-safe base64") from exc
    if len(value) != 32:
        raise KeyWrappingError(f"{label} must decode to exactly 32 bytes")
    return value


class LocalAesKeyWrapper:
    """AES-GCM envelope wrapper with explicit key IDs and rotation epochs."""

    def __init__(
        self,
        *,
        key: str,
        key_id: str = "local-1",
        key_epoch: int = 1,
        previous_keys: dict[str, str] | None = None,
    ) -> None:
        if not key_id or len(key_id) > 128:
            raise KeyWrappingError("transform key ID must contain 1-128 characters")
        if key_epoch < 1:
            raise KeyWrappingError("transform key epoch must be positive")
        self._key_id = key_id
        self._key_epoch = key_epoch
        self._keys = {
            key_id: parse_aes256_key(key, label="YAGAMI_TRANSFORM_KEY"),
            **{
                previous_id: parse_aes256_key(
                    previous_key,
                    label=f"previous transform key {previous_id!r}",
                )
                for previous_id, previous_key in (previous_keys or {}).items()
            },
        }

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def key_epoch(self) -> int:
        return self._key_epoch

    def wrap(self, plaintext_key: bytes, *, context: bytes) -> WrappedDataKey:
        if len(plaintext_key) != 32:
            raise KeyWrappingError("data-encryption key must contain exactly 32 bytes")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._keys[self._key_id]).encrypt(
            nonce,
            plaintext_key,
            self._aad(context, self._key_id, self._key_epoch),
        )
        return WrappedDataKey(
            ciphertext=nonce + ciphertext,
            key_id=self._key_id,
            key_epoch=self._key_epoch,
        )

    def unwrap(self, wrapped_key: WrappedDataKey, *, context: bytes) -> bytes:
        key = self._keys.get(wrapped_key.key_id)
        if key is None:
            raise KeyWrappingError(
                f"transform wrapping key {wrapped_key.key_id!r} is not configured"
            )
        if len(wrapped_key.ciphertext) < 29:
            raise KeyWrappingError("wrapped data-encryption key is malformed")
        nonce, ciphertext = wrapped_key.ciphertext[:12], wrapped_key.ciphertext[12:]
        try:
            plaintext = AESGCM(key).decrypt(
                nonce,
                ciphertext,
                self._aad(context, wrapped_key.key_id, wrapped_key.key_epoch),
            )
        except Exception as exc:  # noqa: BLE001 - authentication has one safe outcome
            raise KeyWrappingError("wrapped data-encryption key authentication failed") from exc
        if len(plaintext) != 32:
            raise KeyWrappingError("unwrapped data-encryption key has an invalid length")
        return plaintext

    @staticmethod
    def _aad(context: bytes, key_id: str, key_epoch: int) -> bytes:
        return (
            b"yagami-transform-dek-v1:"
            + context
            + b":"
            + key_id.encode("utf-8")
            + b":"
            + str(key_epoch).encode("ascii")
        )


def resolve_secret_reference(reference: str, *, label: str) -> str:
    """Resolve ``env:``, ``file:``, or ``keyring:service/account`` references."""
    if not reference:
        return ""
    scheme, separator, target = reference.partition(":")
    if not separator or not target:
        raise ValueError(f"{label} must use env:, file:, or keyring: reference syntax")
    if scheme == "env":
        if not _ENV_NAME.fullmatch(target):
            raise ValueError(f"{label} contains an invalid environment variable name")
        value = os.getenv(target, "")
    elif scheme == "file":
        path = Path(target).expanduser()
        if not path.is_file():
            raise ValueError(f"{label} file does not exist or is not a regular file")
        if path.stat().st_size > _MAX_SECRET_BYTES:
            raise ValueError(f"{label} file exceeds {_MAX_SECRET_BYTES} bytes")
        value = path.read_text(encoding="utf-8").strip()
    elif scheme == "keyring":
        service, slash, account = target.partition("/")
        if not slash or not service or not account:
            raise ValueError(f"{label} keyring reference must be keyring:service/account")
        value = keyring.get_password(service, account) or ""
    else:
        raise ValueError(f"{label} uses unsupported secret provider {scheme!r}")
    if not value:
        raise ValueError(f"{label} resolved to an empty value")
    return value


def resolve_secret(direct_value: str, reference: str, *, label: str) -> str:
    """Prefer a secret reference, retaining direct values for compatibility."""
    return resolve_secret_reference(reference, label=label) if reference else direct_value


def resolve_secret_reference_map(references: dict[str, str], *, label: str) -> dict[str, str]:
    """Resolve an ID-to-reference map used for decrypt-only rotation keys."""
    return {
        key_id: resolve_secret_reference(reference, label=f"{label}[{key_id}]")
        for key_id, reference in references.items()
    }
