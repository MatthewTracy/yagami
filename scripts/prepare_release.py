"""Prepare or validate one lockstep Yagami release.

The release pull-request workflow invokes this script so every Yagami-owned
registry receives artifacts built from one reviewed source revision.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
PYPROJECTS = [
    ROOT / "pyproject.toml",
    ROOT / "integrations" / "langchain-yagami" / "pyproject.toml",
    ROOT / "integrations" / "llama-index-llms-yagami" / "pyproject.toml",
    ROOT / "integrations" / "llama-index-embeddings-yagami" / "pyproject.toml",
]
PACKAGE_INITS = [
    ROOT / "src" / "yagami" / "__init__.py",
    ROOT / "integrations" / "langchain-yagami" / "src" / "langchain_yagami" / "__init__.py",
    ROOT
    / "integrations"
    / "llama-index-llms-yagami"
    / "src"
    / "llama_index"
    / "llms"
    / "yagami"
    / "__init__.py",
    ROOT
    / "integrations"
    / "llama-index-embeddings-yagami"
    / "src"
    / "llama_index"
    / "embeddings"
    / "yagami"
    / "__init__.py",
]


def _replace_once(path: Path, pattern: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"expected one version marker in {path.relative_to(ROOT)}")
    path.write_text(updated, encoding="utf-8")


def _python_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise ValueError(f"{path.relative_to(ROOT)} has no string __version__")


def _project_version(path: Path) -> str:
    match = re.search(
        r"(?ms)^\[project\]\s+.*?^version\s*=\s*\"([^\"]+)\"",
        path.read_text(encoding="utf-8"),
    )
    if match is None:
        raise ValueError(f"{path.relative_to(ROOT)} has no [project] version")
    return match.group(1)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain an object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def versions() -> dict[str, str]:
    result = {str(path.relative_to(ROOT)): _project_version(path) for path in PYPROJECTS}
    result.update({str(path.relative_to(ROOT)): _python_version(path) for path in PACKAGE_INITS})
    chart = (ROOT / "deploy" / "helm" / "yagami" / "Chart.yaml").read_text(encoding="utf-8")
    values = (ROOT / "deploy" / "helm" / "yagami" / "values.yaml").read_text(encoding="utf-8")
    uv_lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    result["deploy/helm/yagami/Chart.yaml"] = re.search(r"(?m)^version:\s*([^\s]+)$", chart).group(
        1
    )
    result["deploy/helm/yagami/appVersion"] = re.search(
        r'(?m)^appVersion:\s*"([^"]+)"$', chart
    ).group(1)
    result["deploy/helm/yagami/values.yaml"] = re.search(
        r'(?m)^\s+tag:\s*"([^"]+)"$', values
    ).group(1)
    yagami_lock = re.search(
        r'(?ms)^\[\[package\]\]\s+name = "yagami"\s+version = "([^"]+)"', uv_lock
    )
    if yagami_lock is None:
        raise ValueError("uv.lock has no Yagami package entry")
    result["uv.lock"] = yagami_lock.group(1)
    package = _load_json(ROOT / "integrations" / "ai-sdk-provider" / "package.json")
    package_lock = _load_json(ROOT / "integrations" / "ai-sdk-provider" / "package-lock.json")
    result["integrations/ai-sdk-provider/package.json"] = str(package["version"])
    result["integrations/ai-sdk-provider/package-lock.json"] = str(package_lock["version"])
    server = _load_json(ROOT / "server.json")
    result["server.json"] = str(server["version"])
    for item in server.get("packages", []):
        if isinstance(item, dict) and item.get("identifier") == "yagami":
            result["server.json package"] = str(item["version"])
    compatibility = _load_json(ROOT / "release" / "compatibility.json")
    result["release/compatibility.json"] = str(compatibility["release"])
    for registry, packages in compatibility["packages"].items():
        for name, value in packages.items():
            result[f"compatibility:{registry}:{name}"] = str(value)
    return result


def validate_lockstep(expected: str | None = None) -> list[str]:
    found = versions()
    target = expected or next(iter(found.values()))
    errors = [
        f"{location}={value}, expected {target}"
        for location, value in found.items()
        if value != target
    ]
    if not SEMVER.fullmatch(target):
        errors.append(f"release version is not stable SemVer: {target}")
    return errors


def update(version: str, notes: str) -> None:
    if not SEMVER.fullmatch(version):
        raise ValueError(f"release version must be stable SemVer: {version}")
    for path in PYPROJECTS:
        _replace_once(
            path,
            r"(?m)^(version\s*=\s*)\"[^\"]+\"",
            rf'\g<1>"{version}"',
        )
    for path in PACKAGE_INITS:
        _replace_once(
            path,
            r'(?m)^(__version__\s*=\s*)"[^"]+"',
            rf'\g<1>"{version}"',
        )
    chart_path = ROOT / "deploy" / "helm" / "yagami" / "Chart.yaml"
    _replace_once(chart_path, r"(?m)^version:\s*[^\s]+$", f"version: {version}")
    _replace_once(chart_path, r'(?m)^appVersion:\s*"[^"]+"$', f'appVersion: "{version}"')
    _replace_once(
        ROOT / "deploy" / "helm" / "yagami" / "values.yaml",
        r'(?m)^(\s+tag:\s*)"[^"]+"$',
        rf'\g<1>"{version}"',
    )
    _replace_once(
        ROOT / "uv.lock",
        r'(?ms)(^\[\[package\]\]\s+name = "yagami"\s+version = ")[^"]+(")',
        rf"\g<1>{version}\g<2>",
    )

    npm_dir = ROOT / "integrations" / "ai-sdk-provider"
    package = _load_json(npm_dir / "package.json")
    package["version"] = version
    _write_json(npm_dir / "package.json", package)
    package_lock = _load_json(npm_dir / "package-lock.json")
    package_lock["version"] = version
    package_lock["packages"][""]["version"] = version
    _write_json(npm_dir / "package-lock.json", package_lock)

    server = _load_json(ROOT / "server.json")
    server["version"] = version
    for item in server.get("packages", []):
        if isinstance(item, dict) and item.get("identifier") == "yagami":
            item["version"] = version
    _write_json(ROOT / "server.json", server)

    compatibility_path = ROOT / "release" / "compatibility.json"
    compatibility = _load_json(compatibility_path)
    old_version = str(compatibility["release"])
    compatibility["release"] = version
    for packages in compatibility["packages"].values():
        for name in packages:
            packages[name] = version
    _write_json(compatibility_path, compatibility)
    _replace_once(
        ROOT / "docs" / "compatibility.md",
        rf"(?m)^The current lockstep release is `{re.escape(old_version)}`\.$",
        f"The current lockstep release is `{version}`.",
    )

    changelog_path = ROOT / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    if f"## [{version}]" not in changelog:
        rendered_notes = "\n".join(
            line if line.startswith("- ") else f"- {line}"
            for line in notes.splitlines()
            if line.strip()
        )
        marker = "## [Unreleased]\n"
        section = (
            f"\n## [{version}] - {date.today().isoformat()}\n\n### Changed\n{rendered_notes}\n"
        )
        if marker not in changelog:
            raise ValueError("CHANGELOG.md has no Unreleased section")
        changelog_path.write_text(
            changelog.replace(marker, marker + section, 1),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        errors = validate_lockstep(args.version)
        if errors:
            raise ValueError("lockstep version errors:\n- " + "\n- ".join(errors))
        print(f"all Yagami artifacts use lockstep version {args.version}")
        return 0
    if not args.notes.strip():
        raise ValueError("--notes is required when preparing a release")
    update(args.version, args.notes)
    errors = validate_lockstep(args.version)
    if errors:
        raise ValueError("release preparation was incomplete:\n- " + "\n- ".join(errors))
    print(f"prepared Yagami {args.version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"release preparation error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
