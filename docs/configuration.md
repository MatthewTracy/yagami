# Configuration without the wall of settings

The demo needs no configuration:

```bash
uvx yagami demo
```

The base install is intentionally small. Add only the capabilities in use:

```bash
python -m pip install "yagami[providers]"  # Anthropic/OpenAI-compatible SDKs
python -m pip install "yagami[ingest]"     # PDF extraction
python -m pip install "yagami[desktop]"    # OS keyring integration
python -m pip install "yagami[all]"        # previous batteries-included behavior
```

For a real local setup, install and start Ollama, then initialize Yagami. The
starter configuration already points to the default loopback endpoint and
model:

```bash
yagami init
ollama pull llama3.2:3b-instruct-q4_K_M
yagami doctor
yagami serve
```

## Minimum remote gateway settings

A non-loopback production bind needs headless mode plus API-key or OIDC
authentication. The smallest API-key setup is:

```dotenv
YAGAMI_HEADLESS=true
YAGAMI_REQUIRE_AUTH=true
YAGAMI_API_KEYS=platform:replace-with-at-least-16-random-characters
```

Then start with `yagami serve --host 0.0.0.0 --allow-remote` behind a trusted
TLS reverse proxy. OIDC-only authentication is also supported; configure both
`YAGAMI_OIDC_ISSUER` and `YAGAMI_OIDC_JWKS_URL` instead of API keys.

## Common choices

- Local model URL/model/trust zone: `YAGAMI_OLLAMA_URL`,
  `YAGAMI_OLLAMA_MODEL`, `YAGAMI_OLLAMA_TRUST_ZONE`.
- Cloud credentials: the provider's API-key variable, preferably injected from
  a secret manager or stored with Yagami's key command.
- Production storage: `YAGAMI_DATABASE_URL` for PostgreSQL and
  `YAGAMI_COORDINATION_URL` for distributed coordination.
- Signed policy enforcement: `YAGAMI_POLICY_BUNDLE_PATH`,
  `YAGAMI_POLICY_PUBLIC_KEY_PATH`, and
  `YAGAMI_POLICY_SIGNATURE_REQUIRED=true`.

## Advanced settings

The annotated [`.env.example`](https://github.com/MatthewTracy/yagami/blob/main/.env.example)
groups identity, database, policy signing, encryption, audit delivery,
approvals, and external detector settings. The
[deployment guide](deployment.md) explains production boundaries and the
[integration guide](integrations.md) covers backend-specific settings.

Environment variables override `config/yagami.toml`. After changing either,
restart Yagami and run `yagami doctor` so endpoint, model, storage, and
credential problems are visible before traffic reaches the gateway.
