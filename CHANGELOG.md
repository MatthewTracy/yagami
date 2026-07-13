# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
below `0.3.0` were backfilled from commit history — see the README's
[Roadmap](README.md#roadmap) for what's planned next rather than what's
shipped.

## [Unreleased]

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
