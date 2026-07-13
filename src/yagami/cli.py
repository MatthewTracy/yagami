"""`yagami` console entry point.

Wraps uvicorn so `pip install -e .` gives you a `yagami` command instead of
having to remember `uvicorn yagami.main:app --reload --reload-dir src/yagami`.

If `ui/dist` exists (see `main.build_app`, which mounts it as static files),
this alone is a working single-process deployment - build the UI once with
`npm run build`, then just run `yagami`. Without a build, the API still
comes up; you'd run `npm run dev` in a second terminal for the dev/hot-reload
UI workflow.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yagami", description="Run the Yagami local-first AI router."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on source changes (development only)",
    )
    args = parser.parse_args()

    import uvicorn

    dist = _project_root() / "ui" / "dist"
    if not dist.exists():
        print(
            "[yagami] ui/dist not found - the API will still run, but no UI is served.\n"
            "[yagami] Run `cd ui && npm run build` for a single-process setup, or\n"
            "[yagami] run `npm run dev` in ui/ separately for the hot-reload dev UI.",
            flush=True,
        )

    uvicorn.run(
        "yagami.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(_project_root() / "src" / "yagami")] if args.reload else None,
    )


if __name__ == "__main__":
    main()
