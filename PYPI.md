# Yagami

**Open-source AI context firewall for governed model, retrieval, memory, and
tool access.**

[![CI](https://github.com/MatthewTracy/yagami/actions/workflows/ci.yml/badge.svg)](https://github.com/MatthewTracy/yagami/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/yagami.svg)](https://pypi.org/project/yagami/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/MatthewTracy/yagami/blob/main/LICENSE)

Yagami is a self-hosted LLM gateway and policy boundary for applications and
AI agents. It classifies context locally, maps destinations to trust zones,
applies versioned policy, governs tool execution, and records content-free
evidence. Existing OpenAI SDK applications can adopt it by changing one
`base_url`.

## Try it in 60 seconds

Choose one command:

```bash
uvx yagami demo
# or: python -m pip install yagami && yagami demo
# or: docker run --rm -p 127.0.0.1:8000:8000 ghcr.io/matthewtracy/yagami:latest yagami demo --host 0.0.0.0 --allow-remote
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The no-credential demo
uses the configured Ollama model when it is installed and otherwise presents a
clearly labeled policy-only fallback. It disables cloud routing while still
exercising classification, policy, lineage, storage, and audit behavior.

For local model-backed answers, install Ollama, run
`ollama pull llama3.2:3b-instruct-q4_K_M`, and restart the demo.

[Watch the two-minute demo](https://github.com/user-attachments/assets/a7be9449-eafc-4acb-99b6-ea39edc43cd2).

## Protect an application

```bash
yagami init
yagami doctor
yagami serve
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="your-yagami-project-key",
)

response = client.chat.completions.create(
    model="yagami-auto",
    messages=[{"role": "user", "content": "Summarize this document."}],
    metadata={"sensitivity": "none", "purpose": "internal-documentation"},
)
print(response.choices[0].message.content)
```

Install `yagami[providers]` when the Yagami process or example client needs
Anthropic/OpenAI-compatible SDKs. PDF extraction uses `yagami[ingest]`, OS
keyring storage uses `yagami[desktop]`, and `yagami[all]` retains the previous
batteries-included behavior.

## What Yagami enforces

- **Sensitive-context containment.** Caller labels and local detectors can
  force PHI, clinical context, credentials, and inherited sensitive history to
  device/private-network backends.
- **Governed tools.** Function tools and MCP calls pass through policy, schema
  checks, risk controls, and optional identity-bound one-time approvals.
- **Retrieval and memory boundaries.** Namespaces, provenance, labels, TTLs,
  quarantine, and policy govern stored and retrieved context.
- **Content-free evidence.** Policy passports, audit chains, metrics, and traces
  carry labels, hashes, reason codes, counts, and timing rather than prompts,
  completions, retrieved text, or tool arguments/results.
- **Self-hosted operations.** Use SQLite locally or PostgreSQL plus distributed
  coordination for production. Containers, Compose, Helm, Prometheus,
  OpenTelemetry, backup/restore, and signed policy bundles are supported.

Yagami exposes OpenAI-compatible Chat Completions and core Responses APIs plus
Streamable HTTP MCP. It works with local Ollama, explicitly configured
llama.cpp, Foundry Local, direct cloud providers, and existing
OpenAI-compatible gateways. Frameworks can use the standard endpoint without
bypassing the same policy and evidence pipeline.

## Limits and trust

Yagami is alpha software and an enforcement component, not a compliance
certification. Detection can miss sensitive or malicious content. Strict
deployments should declare known sensitivity at the caller, fail closed, use a
local-only policy where appropriate, test organization-specific cases, and
review the threat model before production use. Telemetry is disabled by
default.

- [Documentation](https://matthewtracy.github.io/yagami/)
- [Five-minute security tour](https://matthewtracy.github.io/yagami/tour/)
- [Gateway API](https://matthewtracy.github.io/yagami/gateway/)
- [Comparison guide](https://matthewtracy.github.io/yagami/comparison/)
- [Deployment guide](https://matthewtracy.github.io/yagami/deployment/)
- [Threat model](https://matthewtracy.github.io/yagami/threat-model/)
- [Benchmark method and results](https://matthewtracy.github.io/yagami/benchmarks/)
- [Security policy](https://github.com/MatthewTracy/yagami/security/policy)
- [Roadmap](https://github.com/MatthewTracy/yagami/blob/main/docs/roadmap.md)

