"""Validate package metadata before building or publishing a release."""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _package_version() -> str:
    tree = ast.parse((ROOT / "src" / "yagami" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    raise ValueError("src/yagami/__init__.py does not define a string __version__")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    project_version = str(project["version"])
    package_version = _package_version()

    if project_version != package_version:
        raise ValueError(
            f"version mismatch: pyproject.toml={project_version}, package={package_version}"
        )

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked_project = next(
        (package for package in lock["package"] if package["name"] == project["name"]), None
    )
    if locked_project is None or str(locked_project["version"]) != project_version:
        locked_version = None if locked_project is None else locked_project["version"]
        raise ValueError(
            f"uv.lock is stale: project={project_version}, locked project={locked_version}"
        )
    if not re.fullmatch(r"\d+\.\d+\.\d+", project_version):
        raise ValueError(f"release version must be stable SemVer: {project_version}")

    tag = args.tag
    if tag and tag != f"v{project_version}":
        raise ValueError(f"tag {tag!r} does not match package version v{project_version}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{project_version}]" not in changelog:
        raise ValueError(f"CHANGELOG.md has no release section for {project_version}")

    print(f"release metadata valid: v{project_version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"release metadata error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
