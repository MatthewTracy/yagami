from __future__ import annotations

from yagami.backends import registry
from yagami.backends.base import Backend, Capability, Pricing
from yagami.config import YagamiConfig


def test_discover_finds_all_real_backends():
    builders = registry.discover_builders()
    # Every backend we ship should expose a build() — the registry can't
    # see helpers (base/registry/retry), so they shouldn't appear.
    assert "echo" in builders
    assert "ollama" in builders
    assert "anthropic" in builders
    assert "stability" in builders
    assert "openai" in builders
    assert "llama_cpp" in builders
    assert "base" not in builders
    assert "registry" not in builders
    assert "retry" not in builders


def test_build_all_with_no_keys_still_has_local_backends():
    cfg = YagamiConfig()
    backends = registry.build_all(cfg, secrets_get=lambda _name: None)
    # Local backends always build; cloud + llama-cpp need keys/files.
    assert "echo" in backends
    assert "ollama" in backends
    assert "anthropic" not in backends
    assert "stability" not in backends
    assert "openai" not in backends
    # llama_cpp has no model_path configured by default → not built.
    assert "llama_cpp" not in backends


def test_build_all_with_keys_builds_cloud_backends():
    cfg = YagamiConfig()
    fake_secrets = {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "STABILITY_API_KEY": "sk-stab-test",
        "OPENAI_API_KEY": "sk-openai-test",
    }
    backends = registry.build_all(cfg, secrets_get=lambda n: fake_secrets.get(n))
    assert "anthropic" in backends
    assert "stability" in backends
    assert "openai" in backends


def test_every_backend_declares_pricing_attr():
    """Pricing is part of the Backend protocol as of v0.2.13. Without it
    estimate_cost(backend, ...) silently returns 0."""
    cfg = YagamiConfig()
    fake_secrets = {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "STABILITY_API_KEY": "sk-stab-test",
        "OPENAI_API_KEY": "sk-openai-test",
    }
    backends = registry.build_all(cfg, secrets_get=lambda n: fake_secrets.get(n))
    for name, b in backends.items():
        assert hasattr(b, "pricing"), f"{name} missing .pricing"
        assert isinstance(b.pricing, Pricing), f"{name}.pricing is {type(b.pricing)}"


def test_every_backend_implements_protocol():
    cfg = YagamiConfig()
    fake_secrets = {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "STABILITY_API_KEY": "sk-stab-test",
        "OPENAI_API_KEY": "sk-openai-test",
    }
    for b in registry.build_all(cfg, secrets_get=lambda n: fake_secrets.get(n)).values():
        assert isinstance(b, Backend)
        assert isinstance(b.name, str) and b.name
        assert isinstance(b.is_local, bool)
        assert isinstance(b.capabilities, set)


def test_tools_capability_flagged_on_cloud_text_backends():
    cfg = YagamiConfig()
    fake_secrets = {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "OPENAI_API_KEY": "sk-openai-test",
    }
    backends = registry.build_all(cfg, secrets_get=lambda n: fake_secrets.get(n))
    assert Capability.TOOLS in backends["anthropic"].capabilities
    assert Capability.TOOLS in backends["openai"].capabilities


def test_failing_builder_doesnt_crash_others(monkeypatch):
    """build_all swallows builder exceptions so one broken plugin doesn't
    take down the whole stack."""
    real_builders = registry.discover_builders()

    def boom(_cfg, _secrets):
        raise RuntimeError("simulated builder explosion")

    patched = {**real_builders, "boomer": boom}
    monkeypatch.setattr(registry, "discover_builders", lambda: patched)

    cfg = YagamiConfig()
    out = registry.build_all(cfg, secrets_get=lambda _n: None)
    assert "echo" in out
    assert "boomer" not in out
