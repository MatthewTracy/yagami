"""Conditionally bundle the built control surface into release artifacts.

Source and editable installs must work before the React application has been
built. Release jobs build ``ui/dist`` first, at which point this hook places it
at the runtime path expected inside wheels and preserves it in source archives.
"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Add generated UI assets only when they are present."""

    def initialize(self, version: str, build_data: dict) -> None:
        del version
        ui_dist = Path(self.root) / "ui" / "dist"
        if not (ui_dist / "index.html").is_file():
            return
        destination = "yagami/ui_dist" if self.target_name == "wheel" else "ui/dist"
        build_data["force_include"][str(ui_dist)] = destination
