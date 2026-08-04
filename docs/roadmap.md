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
- Microsoft Foundry Local generation through its loopback OpenAI-compatible
  service, with local-trust validation and health diagnostics.
- Hash-chained audit evidence, NDJSON/SIEM streaming, approval notifications,
  Prometheus, and content-free OpenTelemetry GenAI telemetry.
- Asynchronous SQLite/PostgreSQL storage, Alembic migrations, Redis-backed
  distributed limits, multi-replica Helm validation, and verified backup and
  restore commands.
- PyPI, container, Compose, and Helm packaging with checksums, SBOMs,
  provenance attestations, hardened defaults, and Python 3.11-3.14 support.
- An open containment corpus and JSON/JUnit benchmark spanning identifiers,
  secrets, clinical data, RAG contamination, tool policy, and benign controls.

## Next: production hardening and key lifecycle

- A leased durable worker for background Responses jobs so process loss cannot
  strand work and another replica can resume it safely.
- Repeated multi-replica, failover, backup/restore, upgrade, and rollback
  certification against every supported release migration.
- Tested SQLite-to-PostgreSQL data migration tooling and stronger
  database-native tenant isolation guidance.
- Cloud KMS/HSM envelope providers, key epochs and rotation, re-encryption, and
  independently signed audit-export manifests.
- Object-storage audit sinks and end-to-end operational dashboards building on
  the existing durable webhook outbox, retry, backpressure, replay, and
  dead-letter path.

## Then: fleet reliability and interoperability

- Route canaries, sensitivity-aware caching, circuit breakers, SLO dashboards,
  and controlled policy promotion/rollback.
- A production local-engine capability registry for vLLM, llama.cpp, Ollama,
  and multi-node scheduling.
- Richer Responses API parity as the upstream specification evolves, including
  production-grade long-running/background response resumption.
- A2A policy-envelope interoperability and governed agent-to-agent artifacts.

Desktop shells, ambient voice/hotkeys, first-party consumer OAuth apps, LoRA
training, and local image-generation expansion remain deferred until the
gateway has external design partners using it in production-like workloads.
