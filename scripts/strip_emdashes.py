"""One-shot helper that replaces em-dashes with ASCII hyphens across the repo.

Usage: python scripts/strip_emdashes.py

Touches: *.py, *.md, *.toml, *.ts, *.tsx, *.sql, *.yml, *.yaml, *.jsonl
Skips: .venv/, node_modules/, dist/, .git/, ui/dist/

Replacement rules (preserves spacing intent):
  " - " (space emdash space) -> " - "
  "-" (emdash with no spaces)   -> "-"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "venv", "node_modules", "dist", ".git", "build", ".pytest_cache"}
EXTS = {".py", ".md", ".toml", ".ts", ".tsx", ".sql", ".yml", ".yaml", ".jsonl", ".txt"}

EM = "-"  # em-dash


def should_skip(p: Path) -> bool:
    return any(part in SKIP_DIRS for part in p.parts)


def transform(text: str) -> str:
    # Preserve the space-em-dash-space pattern as space-hyphen-space; collapse
    # other forms to a single ASCII hyphen.
    return text.replace(f" {EM} ", " - ").replace(EM, "-")


def main() -> int:
    touched = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in EXTS or should_skip(path):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if EM.encode("utf-8") not in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        new = transform(text)
        if new != text:
            path.write_text(new, encoding="utf-8", newline="\n")
            touched += 1
            print(f"  cleaned: {path.relative_to(ROOT)}")
    print(f"\ntouched {touched} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
