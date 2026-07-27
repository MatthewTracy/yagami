from __future__ import annotations

import ast
from pathlib import Path


def test_python_examples_are_valid_and_do_not_embed_real_credentials():
    root = Path(__file__).parents[1] / "examples"
    examples = sorted(root.rglob("*.py"))
    assert examples
    for path in examples:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))
        assert "sk-proj-" not in source
        assert "-----BEGIN PRIVATE KEY-----" not in source
