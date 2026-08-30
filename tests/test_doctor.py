from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from yagami import doctor
from yagami.config import YagamiConfig


class _Response:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses: dict[str, object] | None = None, *, error: bool = False) -> None:
        self.responses = responses or {}
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, path: str):
        if self.error:
            raise httpx.ConnectError("offline")
        return _Response(self.responses[path])


def _settings(config_path, **overrides):
    values = {
        "anthropic_api_key": "",
        "stability_api_key": "",
        "config_path": str(config_path),
        "api_keys": "",
        "oidc_issuer": "",
        "oidc_jwks_url": "",
        "headless": False,
        "require_auth": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_doctor_reports_ready_with_model_and_optional_extras(
    tmp_path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / "yagami.toml"
    config_path.touch()
    cfg = YagamiConfig()

    async def ollama_ready(_url: str, _model: str) -> tuple[bool, str]:
        return True, "models loaded: 1; default present: True"

    monkeypatch.setattr(doctor, "get_config", lambda: cfg)
    monkeypatch.setattr(doctor, "get_settings", lambda: _settings(config_path))
    monkeypatch.setattr(doctor, "_check_ollama", ollama_ready)
    monkeypatch.setattr(
        doctor.importlib.util,
        "find_spec",
        lambda name: object() if name == "openai" else None,
    )
    monkeypatch.setattr(
        doctor.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=12 * 1024**3),
    )

    assert await doctor.main() == 0
    output = capsys.readouterr().out
    assert "Doctor result: ready" in output
    assert "OpenAI-compatible provider SDK  (installed)" in output
    assert "PDF ingestion  (optional; install `yagami[ingest]` to enable)" in output
    assert "12.0 GB free" in output


@pytest.mark.asyncio
async def test_doctor_prescribes_actions_for_offline_unauthenticated_host(
    tmp_path, monkeypatch, capsys
) -> None:
    cfg = YagamiConfig()
    cfg.foundry_local.enabled = True

    async def ollama_offline(_url: str, _model: str) -> tuple[bool, str]:
        return False, "unreachable: connection refused"

    async def foundry_offline(_url: str, _model: str) -> tuple[bool, str]:
        return False, "unreachable or invalid response: connection refused"

    monkeypatch.setattr(doctor, "get_config", lambda: cfg)
    monkeypatch.setattr(doctor.sys, "version_info", (3, 10))
    monkeypatch.setattr(
        doctor,
        "get_settings",
        lambda: _settings(
            tmp_path / "missing.toml",
            headless=True,
            require_auth=True,
            stability_api_key="configured",
        ),
    )
    monkeypatch.setattr(doctor, "_check_ollama", ollama_offline)
    monkeypatch.setattr(doctor, "_check_foundry_local", foundry_offline)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(
        doctor.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1024**3),
    )

    assert await doctor.main() == 1
    output = capsys.readouterr().out
    assert "start Ollama with `ollama serve`" in output
    assert "install Python 3.11 or newer" in output
    assert "run `foundry service status`" in output
    assert "run `yagami init`" in output
    assert "set YAGAMI_API_KEYS" in output
    assert "set OLLAMA_MODELS" in output
    assert "Doctor result: action required" in output


@pytest.mark.asyncio
async def test_doctor_accepts_oidc_and_a_ready_foundry_backend(
    tmp_path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / "yagami.toml"
    config_path.touch()
    cfg = YagamiConfig()
    cfg.foundry_local.enabled = True

    async def model_missing(_url: str, _model: str) -> tuple[bool, str]:
        return False, "models loaded: 0; default present: False"

    async def foundry_ready(_url: str, _model: str) -> tuple[bool, str]:
        return True, "models loaded: 1; configured present: True"

    monkeypatch.setattr(doctor, "get_config", lambda: cfg)
    monkeypatch.setattr(
        doctor,
        "get_settings",
        lambda: _settings(
            config_path,
            headless=True,
            oidc_issuer="https://identity.example",
            oidc_jwks_url="https://identity.example/jwks.json",
            anthropic_api_key="configured",
        ),
    )
    monkeypatch.setattr(doctor, "_check_ollama", model_missing)
    monkeypatch.setattr(doctor, "_check_foundry_local", foundry_ready)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        doctor.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=8 * 1024**3),
    )

    assert await doctor.main() == 0
    output = capsys.readouterr().out
    assert f"ollama pull {cfg.ollama.model}" in output
    assert "Foundry Local" in output
    assert "headless gateway authentication" in output
    assert "Doctor result: ready" in output


@pytest.mark.asyncio
async def test_ollama_probe_handles_installed_and_unreachable_models(monkeypatch) -> None:
    client = _Client({"http://ollama.test/api/tags": {"models": [{"name": "model-a"}]}})
    monkeypatch.setattr(doctor.httpx, "AsyncClient", lambda **_kwargs: client)

    assert await doctor._check_ollama("http://ollama.test", "model-a") == (
        True,
        "models loaded: 1; default present: True",
    )

    monkeypatch.setattr(
        doctor.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(error=True),
    )
    ok, detail = await doctor._check_ollama("http://ollama.test", "model-a")
    assert not ok
    assert detail.startswith("unreachable:")


@pytest.mark.asyncio
async def test_foundry_probe_accepts_list_and_dict_shapes_and_rejects_invalid(
    monkeypatch,
) -> None:
    responses = {
        "/openai/status": {"status": "running"},
        "/openai/loadedmodels": [{"model": "Phi-4"}],
    }
    monkeypatch.setattr(
        doctor.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(responses),
    )
    ok, detail = await doctor._check_foundry_local("http://foundry.test/v1", "phi-4")
    assert ok
    assert "configured present: True" in detail

    responses["/openai/loadedmodels"] = {"models": ["other-model"]}
    ok, detail = await doctor._check_foundry_local("http://foundry.test/v1", "phi-4")
    assert not ok
    assert "configured present: False" in detail

    responses["/openai/loadedmodels"] = "invalid"
    assert await doctor._check_foundry_local("http://foundry.test/v1", "phi-4") == (
        False,
        "invalid loaded-models response",
    )

    monkeypatch.setattr(
        doctor.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(error=True),
    )
    ok, detail = await doctor._check_foundry_local("http://foundry.test/v1", "phi-4")
    assert not ok
    assert detail.startswith("unreachable or invalid response:")
