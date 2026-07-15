# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
below `0.3.0` were backfilled from commit history — see the
[product roadmap](docs/roadmap.md) for what's planned next rather than what's
shipped.

## [Unreleased]

## [0.6.1] - 2026-07-14

### Security
- Make the Ollama trust boundary explicit. Device mode accepts loopback and
  Docker host-gateway endpoints; other endpoints require an administrator to
  declare the trusted `private_network` zone because Ollama receives prompts
  for classification, generation, and embeddings.
- Revalidate environment-provided Ollama URLs instead of allowing an override
  to bypass configuration validation, and expose the effective zone through
  `yagami doctor` and the local settings surface.

### Changed
- Tighten the PyPI landing page around Yagami's context-firewall positioning,
  compact provider coverage, use a labeled demo link, and reduce duplicated
  navigation.
- Add verified Security and Roadmap project links plus context-firewall,
  AI-security, and data-governance discovery keywords.

### Fixed
- Correct the gateway compatibility guide: Responses API caller-defined
  function tools are implemented and tested.
- Correct the architecture guide to reflect that browser chat now uses the
  same governed gateway service as the public APIs.

## [0.6.0] - 2026-07-14

### Added
- Optional Microsoft Foundry Local generation backend using its loopback
  OpenAI-compatible service, including health diagnostics, UI configuration,
  documentation, and strict local-trust validation.

### Changed
- Condense the GitHub and PyPI landing page around adoption, guarantees,
  integrations, and limitations; move contributor and knowledge-base details
  into the documentation site.
- Identify the MIT copyright holder as Matthew Tracy and Yagami contributors.

### Fixed
- Return JSON-safe validation errors from the configuration API.

## [0.5.0] - 2026-07-14

### Added
- Authenticated Streamable HTTP MCP server exposing governed chat and policy
  preview tools, with project and scope enforcement inherited from the gateway.
- Broader OpenAI Responses API compatibility, including native function calls,
  function outputs, multimodal input, and streaming tool events.
- OIDC/JWT workload identity with strict JWKS, issuer, audience, scope, and
  project-claim validation.
- Deterministic Ed25519-signed policy bundles, declarative policy regression
  tests, schema pinning, and context-firewall injection/trust signals.
- Optional Microsoft Presidio analysis, environment/file/keyring secret
  references, Slack/Teams approval notifications, and generic or Splunk HEC
  audit streaming with configurable fail-closed behavior.
- OpenTelemetry GenAI spans and metrics without prompt or completion content.
- Secure Helm deployment templates, MkDocs documentation, and published API,
  integration, benchmark, deployment, and release-integrity guidance.
- First-class `yagami init`, `doctor`, `demo`, and policy-management commands;
  wheels now include the web control surface and starter configuration.

### Changed
- Unified browser chat and OpenAI-compatible endpoints on one gateway pipeline,
  so routing, policy, lineage, approvals, privacy controls, and persistence are
  applied consistently.
- Expanded support and CI coverage to Python 3.11 through 3.14, raised branch
  coverage enforcement to 80%, and added mypy, CodeQL, dependency review,
  browser tests, Helm validation, and release artifact smoke tests.
- Upgraded the UI to the current Vite/React/Tailwind toolchain and hardened
  HTTP/WebSocket error handling.

### Security
- Prevent sensitive tool results from being sent to a non-local model and add
  untrusted-output containment around retrieved/tool-provided instructions.
- Require authentication for remote admin routes, reject unsafe remote binds,
  constrain knowledge-base paths and uploads, sanitize external errors, and
  harden URL-fetch, middleware, origin, and request-size boundaries.
- Support content-free outbound operational notifications and audit records so
  prompts, completions, tokens, and secrets do not enter collaboration or SIEM
  systems.

### Fixed
- Package all runtime templates and UI assets needed by a clean PyPI install.
- Make headless secret resolution deterministic without probing an unavailable
  desktop keyring.

## [0.4.2] - 2026-07-14

### Fixed
- Use canonical GitHub URLs for every repository link in the packaged README,
  so documentation, contribution, security, license, configuration, and source
  links work when the project description is rendered on PyPI.
- Pass the repository explicitly when creating GitHub Releases from downloaded
  workflow artifacts, where no Git checkout is present.

## [0.4.1] - 2026-07-14

### Added
- OpenAI-compatible `POST /v1/chat/completions`, `GET /v1/models`, and a core
  `POST /v1/responses` text/streaming surface for headless integrations.
- Versioned YAML/JSON policy engine with canonical policy hashes, restrictive
  rule merging, hot reload, caller context, sensitivity hints, shadow mode,
  and `POST /v1/policy/preview`.
- Bearer API keys mapped to project identities through `YAGAMI_API_KEYS`.
- Scoped service accounts, multiple separation-of-duties keys per project,
  hot-reloaded project rate/concurrency/spend/context limits, and authenticated
  metrics/audit operations.
- Content-free context-lineage graphs, AES-GCM local tokenization and
  rehydration, output allow/redact/block DLP, and policy simulation/replay.
- Caller-defined Chat Completions function tools plus durable, short-lived,
  one-time approval capabilities bound to project, purpose, and tool pattern.
- Governed remote MCP over Streamable HTTP with HTTPS enforcement, dedicated
  bearer secrets or OAuth client credentials, audience/resource binding, and
  token caching.
- Project-scoped tamper-evident SHA-256/HMAC audit chains with verification and
  SIEM-friendly NDJSON export.
- A containment benchmark with synthetic PHI/PII/secrets, RAG contamination,
  tool-governance cases, benign controls, and JSON/JUnit output.
- Policy passports on gateway ledger rows, privacy-safe OpenTelemetry span
  attributes, and low-cardinality Prometheus metrics.
- Hardened non-root Docker image, Compose deployment, container CI, and an
  immutable release workflow for wheels and multi-architecture GHCR images.
- Clean-install and runtime smoke tests, high/critical vulnerability gates,
  checksums, SPDX SBOMs, license inventory, and GitHub build-provenance
  attestations for public artifacts.
- OIDC-only PyPI Trusted Publishing behind a dedicated GitHub environment;
  no long-lived package-registry token is stored in GitHub.

### Security
- Explicit remote backend and slash-command routes now run semantic privacy
  classification before cloud egress. Classifier failures fail local for
  automatic routing and refuse explicit remote routes.

### Fixed
- **PHI-history gate now covers every cloud text backend.** It previously
  matched `backend.name == "anthropic"` literally, so `/mistral`, `/groq`,
  `/openrouter`, `/gemini`, and `/openai` (slash or `force_backend`) could
  ship PHI-containing chat history to those clouds. The gate is now
  capability-based (cloud + TEXT); image gen stays exempt as before.
- **Daily spend cap now covers every cloud backend** (same name-list bug),
  including the fast-path and the default route - a cloud `default_backend`
  previously bypassed both gates entirely and now falls back to local with
  an explanatory reason instead.
- **Profile overrides now affect the live spend gate** - it read the base
  `[routing]` config instead of the profile-adjusted one.
- Vision attachments pick the first configured vision-capable backend
  (anthropic, then gemini/openai/openrouter) instead of hard-requiring
  anthropic; a clear error is returned when none is configured.

### Added
- `block_cloud` flag on `[routing]` and per-profile - refuse ALL cloud
  routes unconditionally. This is the correct way to express a zero-cloud
  profile; `daily_spend_cap_usd = 0` means *no cap* (the README previously
  mis-documented it as "no cloud spend").

### Hardened
- MCP tool calls carry a 60s read timeout so a hung server can't hang a turn.
- Folder indexing serializes concurrent `POST /api/kb/index` runs.

## [0.3.0] - 2026-07-13

- OSS project hygiene: CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, issue/PR
  templates.
- Onboarding: `yagami` CLI entry point, one-shot setup scripts, documented
  single-process (`ui/dist` static-mount) quick-try path.
- New backends: Mistral, Groq, OpenRouter, Gemini, via a shared
  `OpenAICompatBackend`. Slash overrides now resolve generically against any
  registered backend name (`/openai`, `/mistral`, ...) instead of a fixed
  alias list - this also fixes `/openai`, which was documented but never
  actually worked.
- Compliance tooling: Privacy Ledger CSV export (`GET /api/decisions/export`)
  and named config profiles (live-switchable, `phi_must_be_local`
  non-overridable by any profile).
- Folder-indexed document knowledge base: `POST /api/kb/index` + `kb.recall`
  skill, backed by a new `kb_documents` corpus separate from chat memory.
- MCP client support: connect to external MCP servers over stdio
  (`[mcp_servers.*]` in config), their tools become Yagami skills
  (`mcp.<server>.<tool>`) automatically.

## [0.2.16] - 2026-06-02

- Cross-session memory retrieval: `needs_recall` classifier signal, memory
  injection into prompts, vacuum job, Memory panel in the UI.

## [0.2.15] - 2026-06-02

- Cross-session memory write path: sqlite-vec storage, `all-minilm`
  embeddings, async embedding worker.

## [0.2.14] - 2026-06-01

- Multi-turn tool-use loop: `Skill` protocol, `calc.eval` and `web.fetch`
  first-party skills, Claude tool-calling integration.

## [0.2.13] - 2026-06-01

- Pluggable backend registry (filesystem discovery), OpenAI backend,
  optional `llama-cpp-python` backend, per-backend `Pricing`.

## [0.2.12] - 2026-06-01

- Settings UI and Stats dashboard — config becomes browser-editable instead
  of TOML-only.

## [0.2.11] - 2026-06-01

- Routing eval suite at 100%, `/reset` slash command, toast notifications,
  draft persistence, keyboard shortcuts, stats, thumbs up/down feedback.

## [0.2.10] - 2026-05-31

- Per-turn classification with a `history_has_phi` gate on cloud routes;
  dropped the earlier "sticky sensitivity floor" design.

## [0.2.9] - 2026-05-31

- OS keyring for API keys, daily spend cap, auto-retry on transient cloud
  errors, file ingest (PDF/MD/TXT), vision input.

## [0.2.8] - 2026-05-30

- PHI floor enforcement, sidebar session management, markdown rendering,
  copy/regenerate, CI workflow, eval diffing.

## [0.2.7] - 2026-05-29

- User-facing routing overrides (slash commands) and routing transparency
  in the UI.

## [0.2.6] - 2026-05-29

- Phi-4 Mini JSON-mode classifier, few-shot prompting, secret/image
  fast-path bypass.

## [0.2.5] - 2026-05-28

- Eval suite scaffolding, classifier prompt sharpening.

## [0.2.4] - 2026-05-28

- UI layout fixes, ledger timings surfaced in the UI, imperative fast-path
  bypass.

## [0.2.3] - 2026-05-28

- Latency reduction: fast-path bypass for trivial prompts, per-request
  system prompt.

## [0.2.2] - 2026-05-28

- Stronger PHI-medical system prompt to break local-model safety refusals
  on legitimate clinical-documentation prompts.

## [0.2.1] - 2026-05-28

- `yagami-phi-medical` prompt variant wired in, textarea chat input.

## [0.2.0] - 2026-05-28

- Privacy Ledger and persistence foundation: routing decisions logged with
  PII/PHI scrubbing.

## [0.1.0] - 2026-05-28

- Initial commit: FastAPI + React local-first AI router skeleton.
