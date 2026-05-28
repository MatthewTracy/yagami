# Yagami

Local-first AI orchestrator. Routes each chat message to the cheapest competent backend:

- **Local** quantized LLM via Ollama for cheap / easy / privacy-sensitive (PHI) prompts.
- **Claude API** for complex reasoning, long context, hard problems.
- **Stability AI** for image generation.

An on-device classifier (the local LLM itself in JSON mode) labels each prompt with
`{intent, sensitivity, complexity}` and a small rules layer picks the backend.
Hard rule: anything classified `phi` / `phi_medical` never leaves the machine.

Inspired by the public description of Lenovo/Motorola's proprietary Qira assistant.
Windows 11 is the primary target for v0.

## Quickstart (Windows 11)

1. **Install [Ollama](https://ollama.com/download/windows)** and pull the default model:
   ```powershell
   ollama pull llama3.2:3b-instruct-q4_K_M
   ```
2. **Python env** (3.11+):
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -e .[dev]
   ```
3. **Env file**:
   ```powershell
   copy .env.example .env
   # edit .env and paste ANTHROPIC_API_KEY (and STABILITY_API_KEY for images)
   ```
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
   Open <http://localhost:5173>. The Vite dev server proxies `/ws` and `/api` to FastAPI.

6. **Sanity check**:
   ```powershell
   python -m yagami.doctor
   ```

## How it routes

`src/yagami/router/policy.py` applies, in order:

1. `sensitivity in {phi, phi_medical}` → forced local (Ollama). PHI-medical also picks
   the `yagami-phi-medical` LoRA variant if defined.
2. `intent == image` → Stability backend.
3. `complexity == high` or `intent == complex_reasoning` → Claude.
4. Otherwise → default local model. `intent == code` attaches the `yagami-code` variant.

Classification source is logged with each decision (`classifier` / `fallback` /
`fallback-after-error`) so you can tell when the model failed to emit valid JSON.

## LoRA variants (Phase 3)

Ollama doesn't hot-swap LoRA adapters at request time, so each variant is a separate
Modelfile-built model. Examples in [config/](config/):

```powershell
# place a code-tuned GGUF LoRA at loras/code.gguf, uncomment ADAPTER, then:
ollama create yagami-code -f config/Modelfile.code
ollama create yagami-phi-medical -f config/Modelfile.phi-medical
```

Variants referenced from the policy live under `[routing.lora_variants]` in
[config/yagami.toml](config/yagami.toml).

## Tests

```powershell
pytest
```

Notable: `tests/test_phi_never_leaves.py` enumerates 20 PHI-shaped prompts and asserts
every one routes to an `is_local=True` backend.

## Windows gotchas

- **PyTorch (only if you add local SDXL later)**: install from CUDA index first so
  transitive `transformers` doesn't pull CPU torch.
  `pip install torch --index-url https://download.pytorch.org/whl/cu124`
- **llama-cpp-python** (only if you add it for real LoRA hot-swap): use the prebuilt
  wheel index `https://abetlen.github.io/llama-cpp-python/whl/cu124`. Source builds
  need VS 2022 Build Tools + CMake + CUDA Toolkit 12.x.
- **Ollama model storage**: defaults to `%USERPROFILE%\.ollama\models`. Relocate with
  `OLLAMA_MODELS` env var if C: is tight.
- **uvicorn --reload** on Windows: pass `--reload-dir src/yagami` to avoid file-watcher
  stalls scanning `node_modules`.
- **HuggingFace long paths**: enable `LongPathsEnabled` in registry if model downloads
  hit `[WinError 206]`.

## Roadmap

- v0.1 (this cut): Phase 0–4. Working chat with intelligent routing + image gen.
- v0.5: Tauri 2 desktop shell, system tray, global hotkey.
- v0.6+: fine-tuned classifier, true LoRA hot-swap (vLLM under WSL or llama-cpp-python),
  local SDXL opt-in, RAG / fused knowledge base, voice "Hey Yagami", screen awareness.
