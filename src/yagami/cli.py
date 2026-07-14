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
import ipaddress
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yagami", description="Run the Yagami private AI policy gateway."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow a non-loopback bind (unsafe without a trusted reverse proxy)",
    )
    parser.add_argument(
        "--trusted-origin",
        action="append",
        default=[],
        metavar="URL",
        help="Browser origin allowed to use chat remotely (repeatable; requires --allow-remote)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on source changes (development only)",
    )
    args = parser.parse_args()

    if args.trusted_origin and not args.allow_remote:
        parser.error("--trusted-origin requires --allow-remote")

    if not _is_loopback_host(args.host):
        if not args.allow_remote:
            parser.error(
                "non-loopback --host requires --allow-remote; Yagami has no built-in "
                "authentication and should normally remain on localhost"
            )
        headless = os.getenv("YAGAMI_HEADLESS", "").casefold() in {"1", "true", "yes", "on"}
        has_api_auth = bool(os.getenv("YAGAMI_API_KEYS"))
        if not (headless and has_api_auth):
            print(
                "[yagami] WARNING: remote access can expose local administration APIs. "
                "Use headless mode with YAGAMI_API_KEYS or a trusted authenticated proxy.",
                file=sys.stderr,
                flush=True,
            )
    if args.trusted_origin:
        os.environ["YAGAMI_TRUSTED_ORIGINS"] = ",".join(args.trusted_origin)

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
