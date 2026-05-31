"""Interactive helper to store an API key in the OS keyring.

Usage:
    python -m yagami.set_key ANTHROPIC_API_KEY
    python -m yagami.set_key STABILITY_API_KEY

Reads from stdin (so the value never appears in shell history) and writes via
the `keyring` library. Once stored, `secrets.get(name)` returns it without
needing the value in .env.
"""

from __future__ import annotations

import getpass
import sys

from . import secrets


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m yagami.set_key <KEY_NAME>", file=sys.stderr)
        return 2
    name = sys.argv[1]
    value = getpass.getpass(f"Paste value for {name} (hidden): ").strip()
    if not value:
        print("(empty value — aborted)", file=sys.stderr)
        return 1
    secrets.set_(name, value)
    print(f"stored {name} in OS keyring under service 'yagami'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
