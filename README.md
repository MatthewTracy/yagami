# Yagami

Local-first AI orchestrator. Routes each chat message to the cheapest
competent backend, keeps sensitive content on-device by default, and
remembers conversations across sessions.

- **Local** quantized LLM via Ollama for cheap / easy / privacy-sensitive prompts.
- **Anthropic Claude** for complex reasoning, long context, vision, tool use.
- **OpenAI** (optional) for an alternate cloud text backend, including
  any OpenAI-compatible base URL (OpenRouter, Groq, Together, Fireworks).
- **Stability AI** for image generation.
- **llama-cpp-python** (optional) for fully-local GGUF inference without
  Ollama.

An on-device classifier (Phi-4 Mini in JSON mode) labels every prompt with
`{intent, sensitivity, complexity, needs_tools, needs_recall}` and a small
rules layer picks the backend. Anything classified `phi`, `phi_medical`, or
`secret` never leaves the machine. The Privacy Ledger surfaces every routing
decision with the reasoning.

Windows 11 is the primary target.

## Status

Pre-release. Working chat, intelligent routing, vision input, image
generation, tool calling, cross-session memory. UI for settings + stats +
memory inspection. No desktop packaging yet (run as a local web app).

## Quickstart (Windows 11)

1. **Install [Ollama](https://ollama.com/download/windows)** and pull the
   default models:
   ```powershell
   ollama pull llama3.2:3b-instruct-q4_K_M
   ollama pull phi4-mini
   ollama pull all-minilm
   ```
   The first is the default generator, the second is the classifier, the
   third (45 MB) powers cross-session memory embeddings.

2. **Python env** (3.11+):
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -e .[dev]
   ```

3. **API keys** (recommended: OS keyring, not `.env`):
   ```powershell
   python -m yagami.set_key ANTHROPIC_API_KEY
   python -m yagami.set_key STABILITY_API_KEY
   # python -m yagami.set_key OPENAI_API_KEY   # optional
   ```
   The keys go to Windows DPAPI / macOS Keychain / Secret Service via the
   `keyring` library. Alternatively copy `.env.example` to `.env` and paste
   the keys there (less safe; only intended for dev).

4. **UI deps**:
   ```powershell
   cd ui
   npm install
   cd ..
   ```

5. **Run** (two terminals):
   ```powershell
   # terminal 1
   uvicorn yagami.main:app --reload --reload-dir src/yagami

   # terminal 2
   cd ui
   npm run dev
   ```
   Open <http://localhost:5173>. Vite dev server proxies `/ws` and `/api`
   to FastAPI.

6. **Sanity check**:
   ```powershell
   python -m yagami.doctor
   ```

## How it routes

`src/yagami/router/policy.py` applies, in order:

1. **Slash override** (`/cloud`, `/local`, `/image`, `/think`, `/code`,
   `/reset`) wins immediately, with one exception: PHI / secret content
   refuses cloud overrides with an explicit error.
2. **Programmatic `force_backend`** from the WS message wins next, same
   PHI guard.
3. **Fast-path bypass** for short prompts that are clearly trivial,
   clearly image-creation, or contain a secret-shaped regex hit. Skips
   the LLM classifier for ~70% of typical turns; cuts time-to-first-token
   to ~300 ms.
4. **LLM classifier** for everything else. Output:
   `{intent, sensitivity, complexity, needs_tools, needs_recall}`.

Routing rules then:

- `sensitivity in {phi, phi_medical, secret}` -> forced local. PHI-medical
  also applies a stronger anti-refusal system prompt.
- `history_has_phi` is checked per turn: cloud text routes (Anthropic,
  OpenAI) are refused when any prior turn in the session contained PHI,
  with the explicit ledger source `+history-phi`. Image gen is allowed
  because Stability only receives the current prompt.
- `intent == image` -> Stability backend (no history sent).
- `needs_tools` or `complexity == high` or `intent == complex_reasoning` ->
  Claude with the multi-turn tool-use loop active.
- `needs_recall` -> retriever fetches top-K relevant past observations
  and injects them as system messages. PHI observations are quarantined
  to PHI sessions.
- Otherwise -> default local model. `intent == code` attaches the
  `yagami-code` LoRA variant if defined.

Every decision is logged via `telemetry/decisions.py` with the user
prompt scrubbed of SSN / credit card / email / phone patterns.

## Features

| Capability | Where it lives |
|---|---|
| Streaming chat | `src/yagami/chat/stream.py`, WebSocket at `/ws/chat` |
| Per-turn classification | `src/yagami/router/{classifier,fast_path}.py` |
| Backend registry (drop-in plugins) | `src/yagami/backends/registry.py` |
| Tool-use loop (Anthropic) | `src/yagami/router/tool_loop.py` |
| First-party skills (`calc.eval`, `web.fetch`) | `src/yagami/skills/` |
| Cross-session memory (sqlite-vec) | `src/yagami/memory/` |
| Cost meter + spend cap | `src/yagami/telemetry/costs.py`, `src/yagami/api/costs.py` |
| Auto-retry on transient errors | `src/yagami/backends/retry.py` |
| File ingest (PDF / MD / TXT) | `src/yagami/ingest/extract.py`, `/api/ingest` |
| Vision input | `ImageAttachment` on `Message`; Claude + OpenAI accept |
| Privacy Ledger UI | `ui/src/components/PrivacyLedger.tsx` |
| Settings modal | `ui/src/components/SettingsModal.tsx` |
| Stats dashboard | `ui/src/components/StatsDashboard.tsx` |
| Memory panel | `ui/src/components/MemoryPanel.tsx` |
| Thumbs-up / down feedback | `tests/test_stats.py` + `/api/decisions/{id}/feedback` |

## Slash commands

Type at the start of a message:

| Command | Effect |
|---|---|
| `/cloud` or `/claude` | Force this turn to Claude. |
| `/local` or `/ollama` | Force this turn to the local model. |
| `/openai` | Force this turn to OpenAI (if configured). |
| `/image` | Force this turn to Stability image gen. |
| `/think` | Force Claude with `complexity=high` hint. |
| `/code` | Stay local; tag as a code task. |
| `/reset` | One-shot bypass of the history-PHI gate. |

All overrides honor the PHI guard. `/cloud` on a PHI prompt is refused
with an explicit error.

## Adding a new backend

Drop one file in `src/yagami/backends/` that exposes:

```python
def build(cfg: YagamiConfig, secrets_get) -> Backend | None:
    key = secrets_get("MY_PROVIDER_API_KEY")
    if not key:
        return None
    return MyProviderBackend(cfg.my_provider, key)
```

Plus a `Backend`-shaped class with `name`, `is_local`, `capabilities`,
`pricing`, `async generate(...)`, `async health()`. The registry
auto-discovers it on next boot. No edit to `main.py`.

## Adding a new skill

Drop one file in `src/yagami/skills/` that exposes:

```python
def build() -> Skill:
    return MySkill()
```

Plus a `Skill`-shaped class with `name`, `description`, `input_schema`
(JSON Schema), `requires_network`, `sensitivity_ceiling`,
`async run(args, ctx) -> SkillResult`. Skills MUST NOT raise; surface
errors via `SkillResult.error`.

## Tests

```powershell
pytest
```

226 tests as of v0.2.16. Notable invariants:

- `tests/test_phi_never_leaves.py` enumerates 20 PHI-shaped prompts and
  asserts every one routes to an `is_local=True` backend.
- `tests/test_fast_path.py` guards the bypass rules so no PHI / secret /
  creative / proof prompt slips past the classifier.
- `tests/test_history_has_phi.py` covers the v0.2.10 per-turn
  classification + history-PHI gate.
- `tests/test_skills.py` covers `calc.eval` safety (rejects `__import__`,
  attribute access, name lookups, etc.).
- `tests/test_tool_loop.py` drives the multi-turn tool-use loop against a
  scripted Anthropic client.
- `tests/test_memory.py` covers the write-gate rules: SECRET never
  written, PHI gets 7-day TTL, defaults 90-day, chunker caps, vacuum.

The routing eval:

```powershell
python -m evals.run_routing
```

Currently 100% (48/48) against a running uvicorn.

## Configuration

Editable from the UI Settings modal, or by hand in `config/yagami.toml`:

```toml
[ollama]
url = "http://localhost:11434"
model = "llama3.2:3b-instruct-q4_K_M"
classifier_model = "phi4-mini"

[anthropic]
model = "claude-sonnet-4-6"
max_tokens = 4096

[openai]
base_url = "https://api.openai.com/v1"
model = "gpt-4.1-mini"
max_tokens = 4096

[stability]
model = "stable-image-core"

[memory]
enabled = true
embedding_model = "all-minilm"

[routing]
default_backend = "ollama"
phi_must_be_local = true       # locked on; server pins true on every PUT
daily_spend_cap_usd = 5.0      # 0 = no cap
long_message_token_threshold = 1500
```

## Privacy posture

- API keys live in the OS keyring by default; `.env` is a fallback.
- The `phi_must_be_local` rule is enforced at routing time AND pinned on
  every config write (defense in depth).
- Memory rows tagged PHI get a 7-day TTL; SECRET-tagged content never
  reaches the embedding worker.
- The Privacy Ledger logs every routing decision with the user text
  scrubbed of SSN / credit card / email / phone patterns.
- Tool-use loop enforces a per-skill `sensitivity_ceiling`. `web.fetch`
  has `Sensitivity.NONE`, so it refuses to run from any PHI session.

## Windows gotchas

- **Ollama model storage**: defaults to `%USERPROFILE%\.ollama\models`.
  Relocate with `OLLAMA_MODELS` env var if C: is tight. The daemon must
  be restarted to pick up the change.
- **uvicorn --reload**: pass `--reload-dir src/yagami` to avoid
  file-watcher stalls scanning `node_modules`.
- **llama-cpp-python**: prebuilt wheel index at
  `https://abetlen.github.io/llama-cpp-python/whl/cu124`. Source builds
  need VS 2022 Build Tools + CMake + CUDA Toolkit 12.x. The backend
  silently skips itself when the package isn't installed.
- **HuggingFace long paths**: enable `LongPathsEnabled` in registry if
  model downloads hit `[WinError 206]`.

## Roadmap

Shipped through v0.2.16 (cross-session memory + memory UI). Planned but
not yet implemented:

- v0.5a: more first-party skills (`kb.recall`, `kb.remember` as Claude
  tools so the LLM can decide when to recall).
- v0.5b: MCP server mount + OAuth (Gmail / Calendar).
- v0.6: user-authored automation routines (cron triggers, content
  triggers, dry-run-by-default).
- v0.7: ambient inputs (global hotkey for clipboard / screenshot
  context, voice in / out via whisper-cpp + piper).
- v0.8+: Tauri 2 desktop shell, system tray, true LoRA hot-swap, local
  SDXL.

## License

MIT - see [LICENSE](LICENSE).
