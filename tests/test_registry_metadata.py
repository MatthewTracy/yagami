"""Check ownership in the description that will actually be uploaded to PyPI."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "validate_registry", Path(__file__).resolve().parents[1] / "scripts" / "validate_registry.py"
)
assert _SPEC is not None and _SPEC.loader is not None
registry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(registry)
NAME = "io.github.MatthewTracy/yagami"
MARKER = f"<!-- mcp-name: {NAME} -->"


def test_repository_readme_cannot_substitute_for_published_description(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nreadme = "PYPI.md"\n')
    (tmp_path / "README.md").write_text(MARKER)
    (tmp_path / "PYPI.md").write_text("# Package description without ownership")
    with pytest.raises(ValueError, match="configured PyPI README"):
        registry.validate_pypi_ownership(NAME)


@pytest.mark.parametrize(
    "readme",
    [
        '"PYPI.md"',
        '{file = "PYPI.md", content-type = "text/markdown"}',
        '{text = "' + MARKER + '", content-type = "text/markdown"}',
    ],
)
def test_configured_description_formats_prove_ownership(tmp_path, monkeypatch, readme):
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(f"[project]\nreadme = {readme}\n")
    (tmp_path / "PYPI.md").write_text(MARKER)
    registry.validate_pypi_ownership(NAME)


@pytest.mark.parametrize("readme", ["{}", '{file = "PYPI.md", text = "ambiguous"}', "{file = 1}"])
def test_invalid_description_configuration_is_rejected(tmp_path, monkeypatch, readme):
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(f"[project]\nreadme = {readme}\n")
    with pytest.raises(ValueError, match="project.readme"):
        registry.validate_pypi_ownership(NAME)


def test_namespace_case_must_match_registry_name(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nreadme = "PYPI.md"\n')
    (tmp_path / "PYPI.md").write_text(MARKER.lower())
    with pytest.raises(ValueError, match="ownership marker"):
        registry.validate_pypi_ownership(NAME)
