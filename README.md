# Yagami

**An open-source, self-hosted context firewall for AI applications and agents.**

[![CI](https://github.com/MatthewTracy/yagami/actions/workflows/ci.yml/badge.svg)](https://github.com/MatthewTracy/yagami/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/yagami.svg)](https://pypi.org/project/yagami/)
[![Python](https://img.shields.io/pypi/pyversions/yagami.svg)](https://pypi.org/project/yagami/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/MatthewTracy/yagami/blob/main/LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/MatthewTracy/yagami/blob/main/docs/roadmap.md)

[Documentation](https://matthewtracy.github.io/yagami/) | [Gateway API](https://matthewtracy.github.io/yagami/gateway/) | [Integrations](https://matthewtracy.github.io/yagami/integrations/) | [Deployment](https://matthewtracy.github.io/yagami/deployment/) | [Roadmap](https://github.com/MatthewTracy/yagami/blob/main/docs/roadmap.md)

Yagami sits between your software and local models, cloud LLMs, retrieval
systems, and tools. It classifies context locally, evaluates versioned policy,
routes only to allowed destinations, inspects outputs, and produces
content-free evidence for each decision.

Existing OpenAI SDK applications can adopt it by changing one `base_url`.
Yagami can run as a headless gateway, in a container or Kubernetes, or with its
included React control surface.

## Try it in 60 seconds

The demo requires no API key, provider account, Ollama model, or Node.js:

```bash
python -m pip install yagami
yagami demo
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Demo mode uses a local
echo backend, blocks cloud routing, and exercises the UI, policy, lineage,
storage, and audit path.

https://github.com/user-attachments/assets/a7be9449-eafc-4acb-99b6-ea39edc43cd2

## Protect an application

Initialize persistent user configuration, check the host, and start Yagami:

```bash
yagami init
yagami doctor
yagami serve
```

Then point an OpenAI client at the gateway:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="your-yagami-project-key",
)

response = client.chat.completions.create(
    model="yagami-auto",
    messages=[{"role": "user", "content": "Summarize this document."}],
    metadata={
        "purpose": "internal-documentation",
        "sensitivity": "none",
        "session_id": "example-session",
    },
)
print(response.choices[0].message.content)
```

Supported caller sensitivity values are `none`, `phi`, `phi_medical`, and
`secret`. A caller hint can make the policy stricter; it cannot lower a
sensitivity detected by Yagami.

For production authentication, policy, and deployment settings, follow the
[deployment guide](https://matthewtracy.github.io/yagami/deployment/).

## Why teams use Yagami

- **Deterministic containment after classification.** Once context is labeled
  as PHI or secret, default policy permits local backends only. Sensitive
  history and tool results inherit the same restriction.
- **One governed data plane.** Chat Completions, Responses, the browser chat,
  and MCP use the same policy, lineage, transformation, output-DLP, budget,
  and audit pipeline.
- **Policy as code.** Preview and replay decisions, run regression cases in
  CI, and promote deterministic Ed25519-signed policy bundles.
- **Evidence without prompt logging.** Policy passports, hash-chained audit
  records, Prometheus metrics, and OpenTelemetry spans carry labels, hashes,
  IDs, and counts rather than prompt or completion content.
- **Model choice without policy duplication.** Route to local engines, direct
  cloud providers, or an existing OpenAI-compatible gateway behind one
  enforcement point.
- **Governed tools.** Evaluate function tools and MCP calls before execution,
  require short-lived one-time approvals, and keep inbound credentials from
  being forwarded to downstream servers.

## Core capabilities

| Area | Included |
|---|---|
| Compatible APIs | OpenAI Chat Completions, core Responses API, Streamable HTTP MCP |
| Identity | Scoped project API keys and OIDC/JWT workload identity |
| Policy | Versioned YAML/JSON rules, restrictive merging, preview, replay, shadow mode, regression tests, signed bundles |
| Privacy | Local classification, caller sensitivity, context lineage, AES-GCM tokenization, rehydration, output DLP, optional Presidio |
| Tools | Function calling, governed built-in skills, stdio and remote MCP, one-time approvals |
| Operations | Spend/rate/concurrency/context limits, health checks, Prometheus, OpenTelemetry, SIEM export, approval webhooks |
| Packaging | Python 3.11-3.14, PyPI, non-root container, Docker Compose, Helm, SBOMs, checksums, and build provenance |

## Models and integrations

Local generation backends:

- [Ollama](https://ollama.com/)
- llama.cpp through the optional `llama-cpp-python` runtime
- Microsoft Foundry Local through its loopback OpenAI-compatible service

Cloud backends:

- Anthropic
- OpenAI
- Mistral
- Groq
- OpenRouter
- Google Gemini
- Stability AI image generation

Yagami also works with LangChain/LangGraph, the Vercel AI SDK, Microsoft
Presidio, Splunk HEC and generic SIEM webhooks, Slack and Teams approval
notifications, and upstream gateways such as LiteLLM, Portkey, Kong, or Envoy.
See the [integration recipes](https://matthewtracy.github.io/yagami/integrations/).

## Microsoft Foundry Local

Foundry Local provides offline, hardware-accelerated inference on supported
Windows and macOS systems. Yagami connects to its local OpenAI-compatible
service without bundling the Foundry CLI or any model:

```powershell
foundry model load qwen2.5-0.5b-instruct
foundry service status
```

Copy the reported endpoint and exact loaded model ID into
`~/.yagami/config/yagami.toml`:

```toml
[foundry_local]
enabled = true
base_url = "http://localhost:5272/v1"
model = "qwen2.5-0.5b-instruct-generic-cpu"
max_tokens = 4096

[routing]
default_backend = "foundry_local"
```

The port can change after the Foundry service restarts. Yagami accepts only
localhost and loopback IPs for this trusted-local backend; use `[upstream]` for
a network-hosted compatible service. Ollama remains the classifier and memory
embedding service in this first integration. Read the full
[Foundry Local setup](https://matthewtracy.github.io/yagami/integrations/#microsoft-foundry-local-preview).

## How enforcement works

```text
application or agent
  -> authentication and project limits
  -> local sensitivity and context-lineage inspection
  -> versioned policy and optional transformation
  -> allowed local model, cloud model, retrieval source, or tool
  -> output DLP
  -> response plus content-free policy passport and audit evidence
```

Policy is the final authority. Slash commands and explicit backend selection
cannot override a sensitive-data restriction. Classifier failures fail local
by default, and cloud routes can be blocked entirely or stopped at a daily
spend cap.

## Important limitations

Yagami is an enforcement component, not a compliance certification. Automated
detection can miss sensitive data. Strict deployments should declare
sensitivity at the caller, use a local-only policy, test organization-specific
cases, encrypt storage at the host or volume layer, and review the
[threat model](https://matthewtracy.github.io/yagami/threat-model/).

The project is alpha. Validate policy and failure behavior against your own
requirements before production use.

## Documentation

- [Start here](https://matthewtracy.github.io/yagami/)
- [Gateway API](https://matthewtracy.github.io/yagami/gateway/)
- [Policy configuration](https://matthewtracy.github.io/yagami/policies/)
- [Integrations](https://matthewtracy.github.io/yagami/integrations/)
- [Deployment](https://matthewtracy.github.io/yagami/deployment/)
- [Local development and extensions](https://matthewtracy.github.io/yagami/development/)
- [Knowledge base](https://matthewtracy.github.io/yagami/knowledge-base/)
- [Architecture](https://matthewtracy.github.io/yagami/architecture/)
- [Threat model](https://matthewtracy.github.io/yagami/threat-model/)
- [Release verification](https://matthewtracy.github.io/yagami/releases/)
- [Benchmarks](https://matthewtracy.github.io/yagami/benchmarks/)
- [Product roadmap](https://github.com/MatthewTracy/yagami/blob/main/docs/roadmap.md)

## Contributing

Focused issues and pull requests are welcome. Read
[CONTRIBUTING.md](https://github.com/MatthewTracy/yagami/blob/main/CONTRIBUTING.md),
the [security policy](https://github.com/MatthewTracy/yagami/blob/main/SECURITY.md),
and the [code of conduct](https://github.com/MatthewTracy/yagami/blob/main/CODE_OF_CONDUCT.md).

## License

[MIT](https://github.com/MatthewTracy/yagami/blob/main/LICENSE) - Copyright
(c) 2026 Matthew Tracy and Yagami contributors.
