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
    if os.getenv("YAGAMI_HEADLESS", "").casefold() in {"1", "true", "yes", "on"} or os.getenv(
        "YAGAMI_DEMO_MODE", ""
    ).casefold() in {"1", "true", "yes", "on"}:
        return False
    try:
        import keyring

        return keyring.get_keyring().priority > 0
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
        except Exception:  # pragma: no cover - depends on platform
            # Keyring exceptions and lookup names can contain secret material.
            log.warning("keyring lookup failed; falling back to environment")
    return os.environ.get(name, "")


def set_(name: str, value: str) -> None:
    """Store a secret in the OS keyring. Used by `python -m yagami.set_key`."""
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("OS key storage requires `pip install 'yagami[desktop]'`") from exc

    keyring.set_password(_SERVICE, name, value)


def clear(name: str) -> None:
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("OS key storage requires `pip install 'yagami[desktop]'`") from exc

    try:
        keyring.delete_password(_SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass
