# Product roadmap

## Shipped in 0.4

- OpenAI-compatible Chat Completions, caller-defined function tools, and core
  Responses API text/streaming surfaces.
- Headless mode, scoped multi-key project identity, project rate/concurrency/
  spend/context limits, Docker/Compose, and release provenance automation.
- Versioned policy documents, restrictive merging, hashes, preview, replay,
  shadow evaluation, policy passports, and fail-closed privacy routing.
- Content-free context lineage across prompts, history, images, tool
  arguments/results, and output.
- Local AES-GCM tokenization/rehydration, output allow/redact/block DLP, and
  encrypted, expiring project/request-bound token mappings.
- Governed stdio/Streamable HTTP MCP with dedicated bearer or OAuth client
  credentials, wildcard deny rules, and one-time tool approvals.
- Project-scoped SHA-256/HMAC audit chains, verification, NDJSON export,
  Prometheus metrics, and privacy-safe OpenTelemetry.
- An open containment corpus and JSON/JUnit benchmark for identifiers,
  secrets, clinical data, RAG contamination, tool policy, and benign controls.

## Next: production identity and storage

- OIDC/JWT workload identity with JWKS rotation, issuer/audience validation,
  group/claim-to-project mapping, and short-lived service credentials.
- Postgres for multi-replica concurrency and database-native row isolation.
- KMS/BYOK envelope encryption, audit-key epochs/rotation, signed export
  manifests, and object-storage/SIEM streaming sinks.
- Background retention enforcement for decisions, approvals, and externally
  configured evidence lifecycles.

## Then: fleet reliability and interoperability

- Route canaries, sensitivity-aware caching, circuit breakers, SLO dashboards,
  and controlled policy promotion/rollback.
- A production local-engine capability registry for vLLM, llama.cpp, Ollama,
  and multi-node scheduling.
- A2A policy-envelope interoperability and governed agent-to-agent artifacts.
- Responses API tool parity, richer multimodal surfaces, and an MCP server
  facade for policy-aware tools.

Desktop shells, ambient voice/hotkeys, first-party consumer OAuth apps, LoRA
training, and local image-generation expansion remain deferred until the
gateway has external design partners using it in production-like workloads.
