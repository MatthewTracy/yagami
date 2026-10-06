"""Validate MCP registry metadata against its pinned official schema."""

from __future__ import annotations

import json
import sys
import tomllib
import urllib.request
from pathlib import Path

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]


def validate_pypi_ownership(name: str) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    readme = project.get("readme")
    if isinstance(readme, str):
        text = (ROOT / readme).read_text(encoding="utf-8")
    elif isinstance(readme, dict):
        if ("file" in readme) == ("text" in readme):
            raise ValueError("project.readme must specify exactly one of file or text")
        if isinstance(readme.get("file"), str):
            text = (ROOT / readme["file"]).read_text(encoding="utf-8")
        elif isinstance(readme.get("text"), str):
            text = readme["text"]
        else:
            raise ValueError("project.readme file or text must be a string")
    else:
        raise ValueError("project.readme must identify the published PyPI description")
    marker = f"mcp-name: {name}"
    if marker not in text:
        raise ValueError(f"the configured PyPI README must contain ownership marker {marker!r}")


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
    validate_pypi_ownership(server["name"])
    print(f"MCP Registry metadata valid: {server['name']} {server['version']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"MCP Registry validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
