# Yagami

**A local-first AI router. Sends each chat message to the cheapest competent backend, keeps sensitive content on-device by default, and remembers across sessions.**

[![CI](https://github.com/MatthewTracy/yagami/actions/workflows/ci.yml/badge.svg)](https://github.com/MatthewTracy/yagami/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Routing eval](https://img.shields.io/badge/routing%20eval-48%2F48-brightgreen.svg)](#tests-and-quality)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](#status)

Most chat UIs assume you've already picked a model. Yagami doesn't. Every turn passes through a small on-device classifier that decides whether the prompt needs a frontier cloud model, a local quantized one, an image generator, or a tool-using agent. Anything tagged PHI or secret never leaves the machine.

If you've ever wished your chat would just route a "what's 2+2" to your laptop and a "design a rate limiter" to Claude, without you flipping a switch, this is that.

## Demo

https://github.com/user-attachments/assets/a7be9449-eafc-4acb-99b6-ea39edc43cd2

---

## What a turn looks like

```
You: what is 2+2
  -> ollama (local)        rules-fast-path                              1.6s

You: write a Python decorator that memoizes
  -> ollama (local)        intent=code, fast-path                       2.4s

You: explain quantum entanglement in 100 words
  -> ollama (local)        intent=simple_qa, fast-path                  3.0s

You: prove the halting problem is undecidable
  -> anthropic (cloud)     complexity=high, intent=complex_reasoning    8.1s

You: what is 14 factorial
  -> anthropic + tools     needs_tools=true (calc.eval: 87178291200)    3.4s

You: Patient Jenny, 54, dyspnea + hypertension. Summarize.
  -> ollama (local)        sensitivity=phi_medical, forced local        2.1s
  -> applies a clinical-documentation system prompt

You: /image a red sailboat at sunset
  -> stability (cloud)     slash override, intent=image                 4.7s
```

Every routing decision lands in a Privacy Ledger panel with the reasoning and timing. Cloud rows highlight amber. The user prompt is scrubbed of SSN / credit card / email / phone patterns before it touches the ledger DB.

---

## Why local-first

- **Privacy by default.** PHI, secrets, and clinical content never leave the device. The `phi_must_be_local` rule is pinned on at routing time AND pinned on every config write (defense in depth).
- **Cost control.** A daily spend cap (`daily_spend_cap_usd`) refuses cloud routes once exceeded; local stays available. Live cost meter in the UI.
- **Right model for the job.** Trivial small-talk doesn't pay a frontier-model round trip. Hard reasoning doesn't get stuck on a 3B local model.
- **Cross-session memory that respects privacy.** PHI rows get a 7-day TTL, never surface in non-PHI sessions, and never reach the embedding worker if tagged `secret`.

---

## Where Yagami fits

| Tool | Role |
|---|---|
| Open WebUI, LibreChat | Chat UIs over an existing model |
| LangChain, LlamaIndex | SDKs you call from your own server |
| Continue, Cursor | IDE-side AI agents |
| **Yagami** | A standalone local web app that routes per turn, with PHI/cost guards and memory built in |

You bring your own keys. Yagami brings the routing, the privacy guarantees, the memory, and the UI.

---

## Status

Alpha. Working chat, intelligent routing, vision input, image generation, multi-turn tool calling, cross-session memory, Settings + Stats + Memory UI. No desktop packaging yet (run as a local web app). Primary target is Windows 11; the Python and React halves run on macOS / Linux too but the install notes below assume Windows.

---

## Quickstart

Requires [Ollama](https://ollama.com/download), Python 3.11+, and Node 20+.
Windows is the primary target (the notes below assume it); the Python and
React halves also run on macOS / Linux, and CI exercises the Python half on
Ubuntu.

### One command

```powershell
# Windows
.\scripts\setup.ps1
```

```bash
# macOS / Linux
./scripts/setup.sh
```

This pulls the three required Ollama models (skipping any already present),
creates/activates a venv, installs the Python package, installs the UI's
`npm` dependencies, and runs `yagami.doctor` so you know immediately if
something's missing. It won't set your cloud API keys for you — see step 3
below for that.

### Manual steps

```powershell
# 1. Install Ollama and pull three models.
ollama pull llama3.2:3b-instruct-q4_K_M   # default local generator
ollama pull phi4-mini                     # classifier
ollama pull all-minilm                    # memory embeddings (45 MB)

# 2. Python env.
python -m venv .venv
.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

# 3. API keys go in the OS keyring (NOT .env). Only ANTHROPIC_API_KEY is
# needed for the default routing table; the rest are optional cloud backends.
python -m yagami.set_key ANTHROPIC_API_KEY
python -m yagami.set_key STABILITY_API_KEY
# python -m yagami.set_key OPENAI_API_KEY       # optional
# python -m yagami.set_key MISTRAL_API_KEY      # optional
# python -m yagami.set_key GROQ_API_KEY         # optional
# python -m yagami.set_key OPENROUTER_API_KEY   # optional
# python -m yagami.set_key GEMINI_API_KEY       # optional

# 4. UI deps.
cd ui
npm install
cd ..
```

Sanity check: `python -m yagami.doctor` verifies the daemon, the models, the keys.

### Run it

**Quick try** (one terminal, no hot reload) — build the UI once, then the
`yagami` CLI serves the API and the built UI together:

```powershell
cd ui ; npm run build ; cd ..
yagami
# open http://localhost:8000
```

**Development** (two terminals, UI hot-reload):

```powershell
yagami --reload         # terminal 1 - API on :8000
cd ui ; npm run dev     # terminal 2 - UI on :5173, proxies to the API
# open http://localhost:5173
```

`yagami --reload` is equivalent to the old
`uvicorn yagami.main:app --reload --reload-dir src/yagami` invocation, just
shorter to type.

---

## How it routes

[`src/yagami/router/policy.py`](src/yagami/router/policy.py) applies, in order:

1. **Slash override** ([`/cloud /local /image /think /code /reset /openai`](#slash-commands)) wins immediately, with one exception: PHI / secret content refuses cloud overrides with an explicit error.
2. **Programmatic `force_backend`** from the WebSocket message wins next, same PHI guard.
3. **Fast-path bypass** for short prompts that are clearly trivial, clearly image-creation, or contain a secret-shaped regex hit. Skips the LLM classifier for roughly 70% of typical turns; cuts time-to-first-token to around 300 ms.
4. **LLM classifier** for everything else. Phi-4 Mini in JSON mode emits `{intent, sensitivity, complexity, needs_tools, needs_recall}`.

The decision tree then:

```
sensitivity in {phi, phi_medical, secret}    -> forced local (Ollama)
intent == image                              -> Stability (prompt only, no history)
needs_tools                                  -> Claude with tool-use loop
complexity == high or complex_reasoning      -> Claude
needs_recall                                 -> inject top-K memories, then route
default                                      -> local Ollama
```

Two refinements:

- **Per-turn `history_has_phi` gate.** Cloud text routes are refused if any prior turn in the session contained PHI, because we'd ship that history along. Image gen and the local model don't trigger this check (Stability only sees the current prompt; local stays local). Use `/reset <prompt>` for a one-shot bypass after the gate fires.
- **Sticky retrieval, not sticky sensitivity.** The earlier sticky-floor design was replaced in v0.2.10: each turn is classified on its own merits, and the history gate is what protects cloud routes.

---

## Slash commands

Type at the start of a message.

| Command | Effect |
|---|---|
| `/cloud` or `/claude` | Force this turn to Claude. |
| `/local` or `/ollama` | Force this turn to the local model. |
| `/image` | Force this turn to Stability image gen. |
| `/think` | Force Claude with `complexity=high` hint. |
| `/code` | Stay local; tag as a code task. |
| `/reset` | One-shot bypass of the history-PHI gate. |
| `/<backend-name>` | Force this turn to any other configured backend, e.g. `/openai`, `/mistral`, `/groq`, `/openrouter`, `/gemini`. Works for any backend currently in `/api/health` — nothing to register. |

All overrides honor the PHI guard. `/cloud` on a PHI prompt is refused with an explicit error.

---

## Features at a glance

| Capability | Where it lives |
|---|---|
| Streaming chat over WebSocket | [`src/yagami/chat/stream.py`](src/yagami/chat/stream.py) |
| Per-turn classification (Phi-4 Mini, JSON mode) | [`src/yagami/router/classifier.py`](src/yagami/router/classifier.py) |
| Fast-path bypass with PHI / secret / image regexes | [`src/yagami/router/fast_path.py`](src/yagami/router/fast_path.py) |
| Backend registry (drop-in plugins) | [`src/yagami/backends/registry.py`](src/yagami/backends/registry.py) |
| 8 backends out of the box (Ollama, llama.cpp local; Anthropic, OpenAI, Mistral, Groq, OpenRouter, Gemini, Stability cloud) | [`src/yagami/backends/`](src/yagami/backends) |
| Multi-turn tool-use loop (Anthropic) | [`src/yagami/router/tool_loop.py`](src/yagami/router/tool_loop.py) |
| First-party skills (`calc.eval`, `web.fetch`) | [`src/yagami/skills/`](src/yagami/skills) |
| Cross-session memory with sqlite-vec + FTS5 fallback | [`src/yagami/memory/`](src/yagami/memory) |
| Folder-indexed document knowledge base (`kb.recall` skill) | [`src/yagami/memory/documents.py`](src/yagami/memory/documents.py), `POST /api/kb/index` |
| MCP client (external MCP servers as skills) | [`src/yagami/skills/mcp_manager.py`](src/yagami/skills/mcp_manager.py), `GET /api/mcp` |
| Cost meter + daily spend cap | [`src/yagami/telemetry/costs.py`](src/yagami/telemetry/costs.py) |
| Auto-retry on transient cloud errors | [`src/yagami/backends/retry.py`](src/yagami/backends/retry.py) |
| File ingest (PDF / MD / TXT, drag-drop) | [`src/yagami/ingest/extract.py`](src/yagami/ingest/extract.py) |
| Vision input (Claude, Gemini, OpenAI, OpenRouter) | `ImageAttachment` on `Message`; auto-picks the first configured vision backend |
| Privacy Ledger panel | [`ui/src/components/PrivacyLedger.tsx`](ui/src/components/PrivacyLedger.tsx) |
| Settings modal (live config edit) | [`ui/src/components/SettingsModal.tsx`](ui/src/components/SettingsModal.tsx) |
| Stats dashboard | [`ui/src/components/StatsDashboard.tsx`](ui/src/components/StatsDashboard.tsx) |
| Memory panel (search / delete) | [`ui/src/components/MemoryPanel.tsx`](ui/src/components/MemoryPanel.tsx) |
| Thumbs up / down feedback | `/api/decisions/{id}/feedback` |
| Privacy Ledger CSV export (compliance/audit) | `GET /api/decisions/export`, [`telemetry/decisions.py`](src/yagami/telemetry/decisions.py) |
| Config profiles (e.g. strict-PHI work vs. permissive personal) | [`src/yagami/config.py`](src/yagami/config.py) `ProfileOverrides` / `effective_routing`, Settings → Profiles tab |

---

## Architecture

```
                          +-------------------+
                          |   React + Vite    |
                          |  (Chat, Settings, |
                          |   Stats, Memory)  |
                          +---------+---------+
                                    | WS / REST
                                    v
+-----------------+  +--------------------------+  +------------------+
|  Privacy        |  |    FastAPI (uvicorn)     |  |  OS Keyring      |
|  Ledger DB      <--+   /ws/chat, /api/*       +-->  (DPAPI / etc.)  |
|  (sqlite)       |  +-----+--------------+-----+  +------------------+
+-----------------+        |              |
                           v              v
            +--------------+----+    +----+-------------+
            | Router policy +   |    |  Cross-session    |
            | fast_path +       |    |  memory:          |
            | classifier        |    |  sqlite-vec       |
            | (Phi-4 Mini JSON) |    |  + embedding      |
            +---+-----+-----+---+    |  worker           |
                |     |     |        +-------------------+
                v     v     v
        +-------+---------------------------------------------+
        |  Backend registry (filesystem-discovered)            |
        |  Echo · Ollama · Anthropic · OpenAI · Stability ·     |
        |  Mistral · Groq · OpenRouter · Gemini · llama-cpp     |
        +-------------------------+------------------------+---+
                                   |
                                   v  (Anthropic only, today)
                     +----------+----------+
                     |  tool_loop:         |
                     |  calc.eval,         |
                     |  web.fetch, ...     |
                     +---------------------+
```

Three operating principles:

1. **PHI guard is the single chokepoint.** Every new write path (memory, skills, future automation) goes through `RoutingPolicy.decide()`. There is no second routing layer to keep in sync.
2. **Backends and skills are filesystem-discovered.** Drop a file in `src/yagami/backends/` or `src/yagami/skills/`, expose a `build(...)` function, and the registry picks it up on boot. No `main.py` edit, no central registration list.
3. **Failures degrade, they don't crash.** Cloud 503 retries. Embedder timeouts mark the row failed. Skill exceptions surface as `SkillResult(ok=False)`. The chat itself only fails if the WebSocket fails.

---

## Adding your own backend

Yagami ships Anthropic, OpenAI, Mistral, Groq, OpenRouter, and Gemini out of
the box (plus local Ollama / llama.cpp). The last four are ~30-line files
because their APIs all speak the same OpenAI-compatible wire format — see
[`src/yagami/backends/openai_compat.py`](src/yagami/backends/openai_compat.py)
for the shared `generate()`/`health()` implementation, and
[`src/yagami/backends/groq.py`](src/yagami/backends/groq.py) for the
shortest real example of subclassing it. If your provider is
OpenAI-compatible too (most are, or offer a compatibility endpoint), that's
the pattern to copy.

For a provider with its own wire format, write `generate()`/`health()`
directly — here's Together AI as a from-scratch example:

```python
# src/yagami/backends/together.py
from __future__ import annotations
from typing import AsyncIterator
from ..config import YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing


def build(cfg: YagamiConfig, secrets_get) -> "TogetherBackend | None":
    key = secrets_get("TOGETHER_API_KEY")
    if not key:
        return None
    return TogetherBackend(key)


class TogetherBackend(Backend):
    name = "together"
    capabilities = {Capability.TEXT, Capability.LONG_CONTEXT}
    is_local = False
    pricing = Pricing(input_per_million_tokens=0.88, output_per_million_tokens=0.88)

    def __init__(self, key: str) -> None: ...

    async def generate(self, messages: list[Message], *, options: BackendOptions) -> AsyncIterator[BackendChunk]:
        ...

    async def health(self) -> bool:
        return True
```

`python -m yagami.set_key TOGETHER_API_KEY`, add a `[together]` section to
`config.py`'s `YagamiConfig` if you need per-provider settings, restart
`yagami`, and it shows up in `/api/health`, the Settings backend dropdown,
and as `/together` (see [Slash commands](#slash-commands) — any registered
backend name works as a slash command automatically, nothing to wire up).

Note: the auto-router's `needs_tools` / `complexity=high` escalation path
(`RoutingPolicy._apply_rules` in
[`src/yagami/router/policy.py`](src/yagami/router/policy.py)) currently only
escalates to `anthropic` specifically, since it's the only backend wired
into `tool_loop`. A new backend is reachable immediately via `/`, the
Settings default-backend dropdown, or `force_backend` — just not via that
one auto-escalation rule yet.

## Adding your own skill

```python
# src/yagami/skills/clipboard_paste.py
from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult


def build() -> Skill:
    return ClipboardPaste()


class ClipboardPaste:
    name = "clipboard.paste"
    description = "Return the current contents of the system clipboard."
    input_schema = {"type": "object", "properties": {}}
    requires_network = False
    sensitivity_ceiling = Sensitivity.PHI_MEDICAL  # safe in any session

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        import pyperclip
        try:
            return SkillResult(ok=True, content=pyperclip.paste())
        except Exception as exc:
            return SkillResult(ok=False, error=str(exc))
```

Skills MUST NOT raise. Use `SkillResult(ok=False, error=...)` to surface failures.

---

## Folder-indexed knowledge base

Point Yagami at a folder of `.pdf` / `.md` / `.txt` / `.log` files and it indexes them (chunk + embed via the same Ollama embedding model memory uses) into a separate corpus from chat memory. The `kb.recall` skill then lets the model search it mid-conversation:

```powershell
# Index a folder (recursive). Re-running replaces a file's chunks, doesn't duplicate them.
curl -X POST http://localhost:8000/api/kb/index -H "Content-Type: application/json" -d '{"path": "C:\\Users\\you\\Documents\\project-docs"}'

# See what's indexed
curl http://localhost:8000/api/kb

# Remove one file's chunks
curl -X DELETE "http://localhost:8000/api/kb/source?path=C:\Users\you\Documents\project-docs\readme.md"
```

No UI panel for this yet - it's API/curl-only for now. A couple of things worth knowing:

- **This is API-first, not queryable through the UI yet.** Indexing is a deliberate, occasional action, not a hot path - it embeds synchronously within the request rather than queuing for the background worker, so a large folder means a slow request, not a silent background failure.
- **Anyone who can reach the local API can make the server read arbitrary files it has OS permission to.** Same trust model as `PUT /api/config` (arbitrary TOML rewrite) already has - this app is single-user, local-first, and assumes you're not exposing the port beyond `127.0.0.1`.
- **Indexed content can reach a cloud backend.** `kb.recall` results flow into the conversation like any tool result - today that means Anthropic specifically, since the tool loop is Anthropic-only (see [Architecture](#architecture)). The classifier only ever evaluates what *you* type, not what's inside indexed documents, so don't index anything you wouldn't want sent to whatever backend handles your tool-use turns.

---

## MCP client support

Yagami connects to external [Model Context Protocol](https://modelcontextprotocol.io) servers over stdio and exposes every tool they offer as a regular Yagami skill - `mcp.<server>.<tool>` - through the exact same `Skill` protocol calc.eval and web.fetch use ([`src/yagami/skills/mcp_manager.py`](src/yagami/skills/mcp_manager.py)). That's the point: any MCP server in the ecosystem becomes usable through Yagami's existing PHI-aware tool-loop gating for free, no per-server integration code.

Configure one or more servers in `config/yagami.toml`:

```toml
[mcp_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\you\\Documents"]

[mcp_servers.everything]
command = "uvx"
args = ["mcp-server-everything"]
```

Restart `yagami` - connections are established once at startup (a server that fails to connect is logged and skipped, it won't crash boot or take down the others). Check what actually connected:

```powershell
curl http://localhost:8000/api/mcp
```

A few things worth knowing:

- **Conservative sensitivity ceiling.** MCP servers are arbitrary, user-configured third-party processes; their tool results flow into the conversation like any tool result (Anthropic-only tool loop today). Every MCP-derived skill gets `sensitivity_ceiling = NONE` - the same floor as `web.fetch` - so it refuses whenever the current turn is flagged sensitive at all.
- **Client only, not server.** Yagami connects *out* to MCP servers; it doesn't expose itself as one yet (a natural follow-up, lower priority since fewer people run Yagami as infrastructure for another tool at this stage).
- **stdio transport only.** No SSE/HTTP MCP servers yet - stdio covers the common case (`npx`/`uvx`-launched local servers) with the least new attack surface.

---

## Tests and quality

```powershell
pytest                          # ~275 tests, ~30s
python -m evals.run_routing     # 48/48 routing decisions, against a running uvicorn
ruff check src tests            # 0 issues
cd ui ; npm run build           # tsc + vite build, clean
```

Key invariants (one test file each):

- **PHI never leaves the device.** 20 PHI-shaped prompts, every one routes `is_local=True`.
- **Secrets never bypass the classifier OR enter the memory index.**
- **Fast-path keeps its mouth shut on creative, complex-reasoning, and ambiguous prompts** so they fall through to the classifier.
- **Tool loop bounds turns at MAX_TURNS=8.** A confused model can't infinite-loop.
- **Memory write gate.** SECRET rejected. PHI gets 7-day TTL. Defaults 90-day. Chunker caps at 8 chunks per message.
- **Cost cap defense in depth.** `phi_must_be_local` is force-pinned to `true` on every `PUT /api/config`, even if the UI sends `false`.

---

## Privacy posture (the short version)

| Asset | How it's protected |
|---|---|
| API keys | OS keyring (Windows DPAPI / macOS Keychain / Secret Service) by default. `.env` is a fallback for dev only. |
| Routing decisions | Logged with user text scrubbed of SSN / credit card / email / phone patterns. |
| PHI prompts | Forced local. Logged with the same scrubber. |
| PHI memory rows | 7-day TTL. Never returned in a non-PHI session. |
| SECRET prompts | Never written to memory. Never reach the embedding worker. |
| Tool calls | Per-skill `sensitivity_ceiling`. `web.fetch` refuses any PHI session. |
| Cross-session leakage | `history_has_phi` gate refuses cloud text routes when any prior turn contained PHI. |
| Config profiles | Can change default backend / spend cap / message-length threshold per profile. Cannot change `phi_must_be_local` - that's pinned server-side regardless of profile. |
| Audit trail | `GET /api/decisions/export` downloads the full Privacy Ledger as CSV (same scrubbing as the UI view, plus cost/token/feedback columns) for compliance review. |

Compliance note: the Privacy Ledger records which config profile was active for every decision, so an audit export answers not just "what did Yagami do" but "under which policy." See [Configuration](#configuration) for defining profiles.

---

## Configuration

Editable in the UI Settings modal, or by hand in [`config/yagami.toml`](config/yagami.toml):

```toml
[ollama]
url = "http://localhost:11434"
model = "llama3.2:3b-instruct-q4_K_M"
classifier_model = "phi4-mini"

[anthropic]
model = "claude-sonnet-4-6"
max_tokens = 4096

[openai]
base_url = "https://api.openai.com/v1"   # any OpenAI-compatible endpoint works here too
model = "gpt-4.1-mini"
max_tokens = 4096

[mistral]
model = "mistral-large-latest"
max_tokens = 4096

[groq]
model = "llama-3.3-70b-versatile"
max_tokens = 4096

[openrouter]
model = "openai/gpt-4o-mini"   # any OpenRouter model id - "vendor/model"
max_tokens = 4096

[gemini]
model = "gemini-2.5-flash"
max_tokens = 8192

[stability]
model = "stable-image-core"

[memory]
enabled = true
embedding_model = "all-minilm"

[routing]
default_backend = "ollama"
phi_must_be_local = true       # locked on; server re-pins this on every PUT
daily_spend_cap_usd = 5.0      # 0 = no cap
long_message_token_threshold = 1500
active_profile = ""            # "" = none; else a key under [profiles.*]

# Named profiles override a subset of [routing] above - default_backend,
# daily_spend_cap_usd, long_message_token_threshold, block_cloud.
# phi_must_be_local is NOT overridable by any profile; it's a hard
# invariant, not a preference.
[profiles.work]
block_cloud = true             # zero cloud on this profile. (NOT the same as
                               # daily_spend_cap_usd = 0 - that means NO cap.)

[profiles.personal]
default_backend = "anthropic"
daily_spend_cap_usd = 10.0

# Connect to external MCP servers over stdio - each tool they expose shows
# up as a Yagami skill, namespaced `mcp.<server>.<tool>`. See "MCP client
# support" below.
[mcp_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\you\\Documents"]
```

Switching `active_profile` (via Settings → Profiles, or `PUT /api/config`) applies on the very next turn - no restart. Every routing decision records which profile (if any) was active; see it per-row in the Privacy Ledger panel and in the CSV export below.

Model and URL changes need a uvicorn restart. Routing changes (default backend, spend cap, threshold) apply on the next turn.

---

## Windows gotchas

- **Ollama model storage** defaults to `%USERPROFILE%\.ollama\models`. Relocate with `OLLAMA_MODELS` env var if C: is tight; restart the Ollama daemon for it to take effect.
- **`uvicorn --reload`**: pass `--reload-dir src/yagami` so the file watcher doesn't try to scan `node_modules`.
- **`llama-cpp-python`** (optional backend): prebuilt wheel index at `https://abetlen.github.io/llama-cpp-python/whl/cu124`. Source builds need VS 2022 Build Tools + CMake + CUDA Toolkit 12.x. The backend silently skips itself when the package isn't installed.
- **HuggingFace long paths**: enable `LongPathsEnabled` in registry if model downloads hit `[WinError 206]`.

---

## Roadmap

Shipped through v0.3.0 (see [CHANGELOG.md](CHANGELOG.md) for what each version added). Planned:

- **v0.5a (partial)** - `kb.recall` shipped, but for a folder-indexed document corpus ([`memory/documents.py`](src/yagami/memory/documents.py), `POST /api/kb/index`), not cross-session chat memory - that's still classifier-driven via `needs_recall`. A `kb.remember` skill (and a `kb.recall` variant over chat memory itself) is still open, so the LLM can choose when to fetch either, not just documents.
- **v0.5b (partial)** - MCP *client* shipped ([MCP client support](#mcp-client-support)). MCP *server* mount (Yagami exposing itself to other MCP clients) + OAuth for Gmail / Calendar are still open.
- **v0.6** - user-authored automation routines (cron triggers, content triggers, dry-run-by-default).
- **v0.7** - ambient inputs: global hotkey (Ctrl+Alt+Y) for clipboard / screenshot context, voice in / out via whisper-cpp + piper.
- **v0.8+** - Tauri 2 desktop shell, system tray, true LoRA hot-swap, local SDXL.

If you want to push any of these forward, the lowest-friction PR is a new skill (one file in `src/yagami/skills/` plus a test).

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, the
pre-PR checklist, and the rules for adding a backend or skill. This project
follows the [Contributor Covenant](CODE_OF_CONDUCT.md). For security issues,
see [SECURITY.md](SECURITY.md) rather than opening a public issue.

---

## Acknowledgments

The shape of the routing layer was inspired by the public description of Lenovo / Motorola's Qira assistant ([CES 2026 coverage](https://www.windowscentral.com/hardware/lenovo/lenovo-qira-hands-on-ces-2026)). Yagami is an independent open-source take on the same idea; no code is shared.

Memory uses [sqlite-vec](https://github.com/asg017/sqlite-vec) for vector storage and Ollama's `/api/embeddings` for the embedding pipeline. The classifier is [Microsoft's Phi-4 Mini](https://huggingface.co/microsoft/Phi-4-mini-instruct) in JSON mode.

---

## License

MIT. See [LICENSE](LICENSE).
