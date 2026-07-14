from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from yagami import cli, doctor
from yagami.paths import configure_default_state


def test_help_lists_first_run_commands(capsys):
    assert cli.main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "yagami init" in output
    assert "yagami doctor" in output
    assert "yagami serve" in output


def test_init_creates_templates_without_overwriting(tmp_path, capsys):
    state = tmp_path / "state"

    assert cli.main(["init", "--directory", str(state)]) == 0
    config = state / "config" / "yagami.toml"
    policy = state / "config" / "policy.yaml"
    projects = state / "config" / "projects.yaml"
    assert config.exists()
    assert policy.exists()
    assert (state / "config" / "policy-tests.yaml").exists()
    assert projects.exists()
    assert (state / ".env.example").exists()
    assert (state / "data").is_dir()

    config.write_text("preserve me", encoding="utf-8")
    assert cli.main(["init", "--directory", str(state)]) == 0
    assert config.read_text(encoding="utf-8") == "preserve me"
    assert "preserved:" in capsys.readouterr().out


def test_configure_default_state_sets_absolute_runtime_paths(tmp_path, monkeypatch):
    state = tmp_path / "state"
    (state / "config").mkdir(parents=True)
    (state / "config" / "yagami.toml").write_text("", encoding="utf-8")
    for name in (
        "YAGAMI_PROJECT_ROOT",
        "YAGAMI_CONFIG_PATH",
        "YAGAMI_POLICY_PATH",
        "YAGAMI_PROJECTS_PATH",
        "YAGAMI_DB_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    configure_default_state(state)

    assert Path(sys.modules["os"].environ["YAGAMI_CONFIG_PATH"]).is_absolute()
    assert sys.modules["os"].environ["YAGAMI_DB_PATH"].endswith("data\\yagami.db")


def test_serve_preserves_legacy_flags_and_calls_uvicorn(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setitem(
        sys.modules, "uvicorn", SimpleNamespace(run=lambda *_a, **kw: calls.append(kw))
    )
    monkeypatch.setattr(cli, "ui_dist", lambda: Path("bundled-ui"))

    assert cli.main(["--host", "127.0.0.1", "--port", "8123"]) == 0

    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8123
    assert calls[0]["ws_max_size"] == 32 * 1024 * 1024


def test_remote_serve_requires_headless_authenticated_mode(monkeypatch):
    monkeypatch.delenv("YAGAMI_HEADLESS", raising=False)
    monkeypatch.delenv("YAGAMI_API_KEYS", raising=False)
    with pytest.raises(SystemExit):
        cli.main(["serve", "--host", "0.0.0.0", "--allow-remote"])


def test_demo_enables_local_no_credential_mode(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_serve", lambda args: 0)

    assert cli.main(["demo", "--port", "8124"]) == 0

    assert sys.modules["os"].environ["YAGAMI_DEMO_MODE"] == "true"
    assert sys.modules["os"].environ["YAGAMI_REQUIRE_AUTH"] == "false"
    assert "no credentials required" in capsys.readouterr().out


def test_doctor_line_formats_required_and_optional_results():
    assert doctor._line("python", True, "3.12").startswith("[OK  ] python")
    assert doctor._line("ollama", False).startswith("[FAIL] ollama")
