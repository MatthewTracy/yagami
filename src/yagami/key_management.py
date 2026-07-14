"""Secret-reference resolution for BYOK and orchestrator-mounted credentials."""

from __future__ import annotations

import os
import re
from pathlib import Path

import keyring

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_SECRET_BYTES = 64 * 1024


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
