# Yagami

**A local-first AI router. Sends each chat message to the cheapest competent backend, keeps sensitive content on-device by default, and remembers across sessions.**

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-226%20passing-brightgreen.svg)](#tests-and-quality)
[![Routing eval](https://img.shields.io/badge/routing%20eval-48%2F48-brightgreen.svg)](#tests-and-quality)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](#status)

Most chat UIs assume you've already picked a model. Yagami doesn't. Every turn passes through a small on-device classifier that decides whether the prompt needs a frontier cloud model, a local quantized one, an image generator, or a tool-using agent. Anything tagged PHI or secret never leaves the machine.

If you've ever wished your chat would just route a "what's 2+2" to your laptop and a "design a rate limiter" to Claude, without you flipping a switch, this is that.

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

> Screenshot placeholder: a `docs/screenshot.png` is the natural next add. Showing the chat with the Privacy Ledger panel open is the strongest single image.

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

```powershell
# 1. Install Ollama (https://ollama.com/download/windows) and pull three models.
ollama pull llama3.2:3b-instruct-q4_K_M   # default local generator
ollama pull phi4-mini                     # classifier
ollama pull all-minilm                    # memory embeddings (45 MB)

# 2. Python env. Requires 3.11+.
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 3. API keys go in the OS keyring (NOT .env).
python -m yagami.set_key ANTHROPIC_API_KEY
python -m yagami.set_key STABILITY_API_KEY
# python -m yagami.set_key OPENAI_API_KEY   # optional

# 4. UI deps.
cd ui
npm install
cd ..

# 5. Run (two terminals).
uvicorn yagami.main:app --reload --reload-dir src/yagami    # terminal 1
cd ui ; npm run dev                                         # terminal 2

# 6. Open http://localhost:5173
```

Sanity check: `python -m yagami.doctor` verifies the daemon, the models, the keys.

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
| `/openai` | Force this turn to OpenAI (if configured). |
| `/image` | Force this turn to Stability image gen. |
| `/think` | Force Claude with `complexity=high` hint. |
| `/code` | Stay local; tag as a code task. |
| `/reset` | One-shot bypass of the history-PHI gate. |

All overrides honor the PHI guard. `/cloud` on a PHI prompt is refused with an explicit error.

---

## Features at a glance

| Capability | Where it lives |
|---|---|
| Streaming chat over WebSocket | [`src/yagami/chat/stream.py`](src/yagami/chat/stream.py) |
| Per-turn classification (Phi-4 Mini, JSON mode) | [`src/yagami/router/classifier.py`](src/yagami/router/classifier.py) |
| Fast-path bypass with PHI / secret / image regexes | [`src/yagami/router/fast_path.py`](src/yagami/router/fast_path.py) |
| Backend registry (drop-in plugins) | [`src/yagami/backends/registry.py`](src/yagami/backends/registry.py) |
| Multi-turn tool-use loop (Anthropic) | [`src/yagami/router/tool_loop.py`](src/yagami/router/tool_loop.py) |
| First-party skills (`calc.eval`, `web.fetch`) | [`src/yagami/skills/`](src/yagami/skills) |
| Cross-session memory with sqlite-vec + FTS5 fallback | [`src/yagami/memory/`](src/yagami/memory) |
| Cost meter + daily spend cap | [`src/yagami/telemetry/costs.py`](src/yagami/telemetry/costs.py) |
| Auto-retry on transient cloud errors | [`src/yagami/backends/retry.py`](src/yagami/backends/retry.py) |
| File ingest (PDF / MD / TXT, drag-drop) | [`src/yagami/ingest/extract.py`](src/yagami/ingest/extract.py) |
| Vision input (Claude + OpenAI) | `ImageAttachment` on `Message` |
| Privacy Ledger panel | [`ui/src/components/PrivacyLedger.tsx`](ui/src/components/PrivacyLedger.tsx) |
| Settings modal (live config edit) | [`ui/src/components/SettingsModal.tsx`](ui/src/components/SettingsModal.tsx) |
| Stats dashboard | [`ui/src/components/StatsDashboard.tsx`](ui/src/components/StatsDashboard.tsx) |
| Memory panel (search / delete) | [`ui/src/components/MemoryPanel.tsx`](ui/src/components/MemoryPanel.tsx) |
| Thumbs up / down feedback | `/api/decisions/{id}/feedback` |

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
        +-------+ +---+--+ +-----------+ +-------+ +----------+
        | Echo  | |Ollama| | Anthropic | |OpenAI | | Stability|
        +-------+ +------+ +----+------+ +-------+ +----------+
                                |
                                v
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

```python
# src/yagami/backends/mistral.py
from __future__ import annotations
from typing import AsyncIterator
from ..config import YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing


def build(cfg: YagamiConfig, secrets_get) -> "MistralBackend | None":
    key = secrets_get("MISTRAL_API_KEY")
    if not key:
        return None
    return MistralBackend(key)


class MistralBackend(Backend):
    name = "mistral"
    capabilities = {Capability.TEXT, Capability.LONG_CONTEXT, Capability.TOOLS}
    is_local = False
    pricing = Pricing(input_per_million_tokens=2.0, output_per_million_tokens=6.0)

    def __init__(self, key: str) -> None: ...

    async def generate(self, messages: list[Message], *, options: BackendOptions) -> AsyncIterator[BackendChunk]:
        ...

    async def health(self) -> bool:
        return True
```

`python -m yagami.set_key MISTRAL_API_KEY`, restart uvicorn, and Mistral shows up in `/api/health` and in the Settings backend dropdown.

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

## Tests and quality

```powershell
pytest                          # 226 tests, ~15s
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
base_url = "https://api.openai.com/v1"   # override for OpenRouter / Groq / Together
model = "gpt-4.1-mini"
max_tokens = 4096

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
```

Model and URL changes need a uvicorn restart. Routing changes (default backend, spend cap, threshold) apply on the next turn.

---

## Windows gotchas

- **Ollama model storage** defaults to `%USERPROFILE%\.ollama\models`. Relocate with `OLLAMA_MODELS` env var if C: is tight; restart the Ollama daemon for it to take effect.
- **`uvicorn --reload`**: pass `--reload-dir src/yagami` so the file watcher doesn't try to scan `node_modules`.
- **`llama-cpp-python`** (optional backend): prebuilt wheel index at `https://abetlen.github.io/llama-cpp-python/whl/cu124`. Source builds need VS 2022 Build Tools + CMake + CUDA Toolkit 12.x. The backend silently skips itself when the package isn't installed.
- **HuggingFace long paths**: enable `LongPathsEnabled` in registry if model downloads hit `[WinError 206]`.

---

## Roadmap

Shipped through v0.2.16. Planned:

- **v0.5a** - more first-party skills (`kb.recall`, `kb.remember`) so the LLM can choose when to fetch memory rather than relying on classifier `needs_recall`.
- **v0.5b** - Model Context Protocol (MCP) server mount + OAuth for Gmail / Calendar.
- **v0.6** - user-authored automation routines (cron triggers, content triggers, dry-run-by-default).
- **v0.7** - ambient inputs: global hotkey (Ctrl+Alt+Y) for clipboard / screenshot context, voice in / out via whisper-cpp + piper.
- **v0.8+** - Tauri 2 desktop shell, system tray, true LoRA hot-swap, local SDXL.

If you want to push any of these forward, the lowest-friction PR is a new skill (one file in `src/yagami/skills/` plus a test).

---

## Contributing

PRs welcome. Before opening one:

```powershell
pytest                          # all tests must pass
ruff check src tests            # 0 warnings
python -m evals.run_routing     # 48/48; if you change routing, update the fixtures
cd ui ; npx tsc --noEmit ; npm run build
```

For larger changes (new backend, new skill, schema migration), opening an issue first is appreciated.

---

## Acknowledgments

The shape of the routing layer was inspired by the public description of Lenovo / Motorola's Qira assistant ([CES 2026 coverage](https://www.windowscentral.com/hardware/lenovo/lenovo-qira-hands-on-ces-2026)). Yagami is an independent open-source take on the same idea; no code is shared.

Memory uses [sqlite-vec](https://github.com/asg017/sqlite-vec) for vector storage and Ollama's `/api/embeddings` for the embedding pipeline. The classifier is [Microsoft's Phi-4 Mini](https://huggingface.co/microsoft/Phi-4-mini-instruct) in JSON mode.

---

## License

MIT. See [LICENSE](LICENSE).
