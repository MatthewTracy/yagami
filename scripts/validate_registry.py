"""Validate MCP registry metadata against its pinned official schema."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    schema_url = server.get("$schema")
    if not isinstance(schema_url, str) or not schema_url.startswith(
        "https://static.modelcontextprotocol.io/schemas/"
    ):
        raise ValueError("server.json must pin an official MCP Registry schema")
    with urllib.request.urlopen(schema_url, timeout=30) as response:  # noqa: S310
        schema = json.load(response)
    Draft7Validator(schema).validate(server)
    marker = f"mcp-name: {server['name']}"
    if marker not in (ROOT / "README.md").read_text(encoding="utf-8"):
        raise ValueError(f"README.md must contain the PyPI ownership marker {marker!r}")
    print(f"MCP Registry metadata valid: {server['name']} {server['version']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"MCP Registry validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
