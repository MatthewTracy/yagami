# Product roadmap

Yagami's focus is the private context and policy plane between applications,
models, retrieval systems, and tools. This roadmap separates implemented
capabilities from work that still needs production design partners.

## Shipped

- OpenAI-compatible Chat Completions and Responses APIs with function calls,
  function outputs, multimodal input, and streaming tool events.
- Authenticated MCP client and server surfaces governed by the same project,
  policy, approval, privacy, lineage, rate, and spend controls.
- API keys plus OIDC/JWT workload identity with JWKS, issuer, audience, scope,
  and project-claim validation.
- Versioned policy documents, restrictive merging, preview/replay, shadow
  evaluation, regression tests, signed policy bundles, schema pinning, and
  policy passports.
- Context trust/injection signals, sensitive tool-result containment, local
  tokenization/rehydration, output DLP, and optional Presidio analysis.
- Hash-chained audit evidence, NDJSON/SIEM streaming, approval notifications,
  Prometheus, and content-free OpenTelemetry GenAI telemetry.
- PyPI, container, Compose, and Helm packaging with checksums, SBOMs,
  provenance attestations, hardened defaults, and Python 3.11-3.14 support.
- An open containment corpus and JSON/JUnit benchmark spanning identifiers,
  secrets, clinical data, RAG contamination, tool policy, and benign controls.

## Next: production storage and key lifecycle

- Postgres for multi-replica concurrency, migrations, database-native tenant
  isolation, backup/restore, and tested SQLite-to-Postgres migration tooling.
- Cloud KMS/HSM envelope providers, key epochs and rotation, re-encryption, and
  independently signed audit-export manifests.
- Background retention enforcement for decisions, approvals, token mappings,
  and externally configured evidence lifecycles.
- Object-storage audit sinks with delivery queues, retry/dead-letter handling,
  backpressure, and end-to-end operational dashboards.

## Then: fleet reliability and interoperability

- Route canaries, sensitivity-aware caching, circuit breakers, SLO dashboards,
  and controlled policy promotion/rollback.
- A production local-engine capability registry for vLLM, llama.cpp, Ollama,
  and multi-node scheduling.
- Richer Responses API parity as the upstream specification evolves, including
  long-running/background response lifecycle support.
- A2A policy-envelope interoperability and governed agent-to-agent artifacts.

Desktop shells, ambient voice/hotkeys, first-party consumer OAuth apps, LoRA
training, and local image-generation expansion remain deferred until the
gateway has external design partners using it in production-like workloads.
