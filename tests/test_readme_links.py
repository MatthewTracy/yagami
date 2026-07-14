import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")
PORTABLE_PREFIXES = ("#", "https://", "http://", "mailto:")
REPOSITORY_PATH = ("MatthewTracy", "yagami")


def _readme_targets() -> list[str]:
    return MARKDOWN_LINK.findall(README.read_text(encoding="utf-8"))


def test_packaged_readme_has_no_repository_relative_links() -> None:
    relative_targets = sorted(
        {target for target in _readme_targets() if not target.startswith(PORTABLE_PREFIXES)}
    )

    assert relative_targets == []


def test_canonical_github_readme_links_point_to_local_paths() -> None:
    checked_paths: list[Path] = []

    for target in _readme_targets():
        parsed = urlsplit(target)
        parts = tuple(filter(None, parsed.path.split("/")))
        if parsed.netloc != "github.com" or parts[:2] != REPOSITORY_PATH:
            continue
        if len(parts) < 5 or parts[2] not in {"blob", "tree"} or parts[3] != "main":
            continue

        local_path = ROOT.joinpath(*map(unquote, parts[4:]))
        checked_paths.append(local_path)
        assert local_path.exists(), f"README link points to a missing path: {target}"

    assert ROOT / "docs" / "roadmap.md" in checked_paths
    assert ROOT / "CONTRIBUTING.md" in checked_paths
