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
import asyncio
import ipaddress
import os
import shutil
import sys
from pathlib import Path

from .paths import configure_default_state, default_state_dir, project_root, template_root, ui_dist


def _project_root() -> Path:
    """Compatibility alias for downstream callers."""
    return project_root()


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "on"}


def _remote_auth_configured() -> bool:
    api_keys = bool(os.getenv("YAGAMI_API_KEYS"))
    oidc = bool(os.getenv("YAGAMI_OIDC_ISSUER") and os.getenv("YAGAMI_OIDC_JWKS_URL"))
    return api_keys or oidc


def _build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yagami serve", description="Run the Yagami private AI policy gateway."
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
        help=(
            "Additional browser Origin allowed to open the local chat socket "
            "(repeatable; not authentication; requires --allow-remote)"
        ),
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on source changes (development only)",
    )
    return parser


def _serve(argv: list[str]) -> int:
    parser = _build_serve_parser()
    args = parser.parse_args(argv)

    if args.trusted_origin and not args.allow_remote:
        parser.error("--trusted-origin requires --allow-remote")

    if not _is_loopback_host(args.host):
        if not args.allow_remote:
            parser.error(
                "non-loopback --host requires --allow-remote; Yagami should normally remain "
                "on localhost unless headless API authentication is configured"
            )
        headless = _env_enabled("YAGAMI_HEADLESS")
        container_demo = _env_enabled("YAGAMI_CONTAINER") and _env_enabled("YAGAMI_DEMO_MODE")
        if not container_demo and not (headless and _remote_auth_configured()):
            parser.error(
                "remote binding requires YAGAMI_HEADLESS=true plus YAGAMI_API_KEYS or OIDC; "
                "the only unauthenticated exception is the isolated container demo"
            )
        if container_demo:
            print(
                "[yagami] container demo is unauthenticated; publish port 8000 to 127.0.0.1 only",
                flush=True,
            )
    if args.trusted_origin:
        os.environ["YAGAMI_TRUSTED_ORIGINS"] = ",".join(args.trusted_origin)

    import uvicorn

    dist = ui_dist()
    if dist is None and not _env_enabled("YAGAMI_HEADLESS"):
        print(
            "[yagami] UI assets are unavailable; the API will still run.\n"
            "[yagami] Source developers can run `cd ui && npm run build`.",
            flush=True,
        )

    uvicorn.run(
        "yagami.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(project_root() / "src" / "yagami")] if args.reload else None,
        ws_max_size=32 * 1024 * 1024,
    )
    return 0


def _init(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="yagami init", description="Create a safe per-user Yagami configuration."
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=default_state_dir(),
        help="State directory (default: %(default)s)",
    )
    parser.add_argument("--force", action="store_true", help="Replace existing template files")
    args = parser.parse_args(argv)
    source = template_root()
    if source is None:
        parser.error("installation does not contain initialization templates")
    target = args.directory.expanduser().resolve()
    created: list[Path] = []
    skipped: list[Path] = []
    for relative in (
        Path("config/yagami.toml"),
        Path("config/policy.yaml"),
        Path("config/policy-tests.yaml"),
        Path("config/projects.yaml"),
        Path(".env.example"),
    ):
        source_file = source / relative
        destination = target / relative
        if not source_file.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not args.force:
            skipped.append(destination)
            continue
        shutil.copy2(source_file, destination)
        created.append(destination)
    (target / "data").mkdir(parents=True, exist_ok=True)
    print(f"Yagami state initialized at {target}")
    print(f"  created: {len(created)}; preserved: {len(skipped)}")
    print("Next: run `yagami doctor`, then `yagami serve`.")
    return 0


def _doctor(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="yagami doctor", description="Check this installation.")
    parser.parse_args(argv)
    from .doctor import main as doctor_main

    return asyncio.run(doctor_main())


def _database(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="yagami db",
        description="Create and verify offline Yagami database backups.",
    )
    commands = parser.add_subparsers(dest="database_command", required=True)
    backup_parser = commands.add_parser("backup", help="Create and verify a backup")
    backup_parser.add_argument("--source", type=Path, default=Path("yagami.db"))
    backup_parser.add_argument("--database-url", default=os.getenv("YAGAMI_DATABASE_URL", ""))
    backup_parser.add_argument("--output", type=Path, required=True)
    backup_parser.add_argument("--force", action="store_true")
    verify_parser = commands.add_parser("verify", help="Verify a backup archive")
    verify_parser.add_argument("backup", type=Path)
    verify_parser.add_argument(
        "--format",
        choices=("sqlite", "postgresql"),
        default="sqlite",
    )
    restore_parser = commands.add_parser(
        "restore-sqlite",
        help="Verify and restore a SQLite backup while Yagami is stopped",
    )
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("--target", type=Path, required=True)
    restore_parser.add_argument("--force", action="store_true")
    migrate_parser = commands.add_parser(
        "migrate",
        help="Apply packaged Alembic migrations to PostgreSQL",
    )
    migrate_parser.add_argument(
        "--database-url",
        default=os.getenv("YAGAMI_DATABASE_URL", ""),
        required=not bool(os.getenv("YAGAMI_DATABASE_URL")),
    )
    args = parser.parse_args(argv)

    from .storage.backup import (
        backup_postgresql,
        backup_sqlite,
        restore_sqlite_backup,
        verify_postgresql_backup,
        verify_sqlite_backup,
    )

    if args.database_command == "backup":
        result = (
            backup_postgresql(args.database_url, args.output, force=args.force)
            if args.database_url
            else backup_sqlite(args.source, args.output, force=args.force)
        )
    elif args.database_command == "verify":
        result = (
            verify_postgresql_backup(args.backup)
            if args.format == "postgresql"
            else verify_sqlite_backup(args.backup)
        )
    elif args.database_command == "restore-sqlite":
        result = restore_sqlite_backup(args.backup, args.target, force=args.force)
    else:
        from .storage.migrate import upgrade_database

        upgrade_database(args.database_url)
        print("Database migration reached Alembic head")
        return 0
    print(f"Verified {result['format']} backup: {result['path']}")
    return 0


def _policy(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="yagami policy", description="Test, sign, and verify Yagami policies."
    )
    commands = parser.add_subparsers(dest="policy_command", required=True)

    test_parser = commands.add_parser("test", help="Run declarative policy regression tests")
    test_parser.add_argument("--policy", type=Path, default=Path("config/policy.yaml"))
    test_parser.add_argument("--cases", type=Path, default=Path("config/policy-tests.yaml"))

    key_parser = commands.add_parser("keygen", help="Create an Ed25519 policy signing key")
    key_parser.add_argument("--private-key", type=Path, required=True)
    key_parser.add_argument("--public-key", type=Path, required=True)
    key_parser.add_argument("--force", action="store_true")

    bundle_parser = commands.add_parser("bundle", help="Create a signed policy bundle")
    bundle_parser.add_argument("--policy", type=Path, required=True)
    bundle_parser.add_argument("--private-key", type=Path, required=True)
    bundle_parser.add_argument("--output", type=Path, required=True)

    verify_parser = commands.add_parser("verify", help="Verify a signed policy bundle")
    verify_parser.add_argument("--bundle", type=Path, required=True)
    verify_parser.add_argument("--public-key", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.policy_command == "test":
        from .policy.testing import run_suite

        results = run_suite(args.policy, args.cases)
        for result in results:
            marker = "PASS" if result.passed else "FAIL"
            print(f"[{marker}] {result.name}")
            for failure in result.failures:
                print(f"       {failure}")
        failed = sum(not result.passed for result in results)
        print(f"Policy tests: {len(results) - failed} passed, {failed} failed")
        return int(bool(failed))
    if args.policy_command == "keygen":
        from .policy.bundle import generate_keypair

        generate_keypair(args.private_key, args.public_key, force=args.force)
        print(f"Created policy verification key: {args.public_key}")
        print(f"Keep the private signing key secret: {args.private_key}")
        return 0
    if args.policy_command == "bundle":
        from .policy.bundle import build_bundle

        manifest = build_bundle(args.policy, args.private_key, args.output)
        print(f"Created signed policy bundle: {args.output}")
        print(f"Policy digest: {manifest['policy']['source_sha256']}")
        return 0
    from .policy.bundle import verify_bundle

    manifest = verify_bundle(args.bundle, args.public_key)
    print(
        f"Verified policy bundle {manifest['policy']['id']} version {manifest['policy']['version']}"
    )
    return 0


def _print_help() -> None:
    print(
        "Yagami private AI policy gateway\n\n"
        "Commands:\n"
        "  yagami init      Create ~/.yagami with safe starter configuration\n"
        "  yagami doctor    Check config, storage, Ollama, and optional providers\n"
        "  yagami demo      Launch a no-credential, local-only interactive demo\n"
        "  yagami db        Create, verify, and restore offline backups\n"
        "  yagami policy    Test and cryptographically sign policy bundles\n"
        "  yagami serve     Start the API and bundled control surface\n\n"
        "Compatibility: `yagami --host ...` still starts the server.\n"
        "Run `yagami <command> --help` for command-specific options."
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else ""
    if command in {"-h", "--help", "help"}:
        _print_help()
        return 0
    if command == "init":
        return _init(args[1:])
    if command == "doctor":
        configure_default_state()
        return _doctor(args[1:])
    if command == "db":
        configure_default_state()
        return _database(args[1:])
    if command == "policy":
        configure_default_state()
        return _policy(args[1:])
    if command == "demo":
        os.environ["YAGAMI_DEMO_MODE"] = "true"
        os.environ["YAGAMI_HEADLESS"] = "false"
        os.environ["YAGAMI_REQUIRE_AUTH"] = "false"
        os.environ.setdefault("YAGAMI_DB_PATH", str(default_state_dir() / "data" / "demo.db"))
        print(
            "[yagami] demo: using the configured Ollama model when installed; "
            "policy-only fallback otherwise; cloud disabled; no credentials required"
        )
        return _serve(args[1:])
    if command == "serve":
        args = args[1:]
    configure_default_state()
    return _serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
