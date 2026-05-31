"""Read API keys from the OS keyring (Windows DPAPI / macOS Keychain / Secret
Service on Linux) with a fall-back to environment variables. Falls back to .env
loaded by pydantic-settings if neither has a value.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

log = logging.getLogger("yagami.secrets")

_SERVICE = "yagami"


def _backend_available() -> bool:
    try:
        import keyring  # noqa: F401

        return True
    except Exception:
        return False


@lru_cache(maxsize=16)
def get(name: str) -> str:
    """Return the secret value, checking OS keyring first then env."""
    if _backend_available():
        try:
            import keyring

            value = keyring.get_password(_SERVICE, name)
            if value:
                return value
        except Exception as exc:  # pragma: no cover - depends on platform
            log.warning("keyring lookup for %s failed (%s); falling back to env", name, exc)
    return os.environ.get(name, "")


def set_(name: str, value: str) -> None:
    """Store a secret in the OS keyring. Used by `python -m yagami.set_key`."""
    import keyring

    keyring.set_password(_SERVICE, name, value)


def clear(name: str) -> None:
    import keyring

    try:
        keyring.delete_password(_SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass
