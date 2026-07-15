# Local development and extensions

## Source setup

Python 3.11 or newer and Node.js 22.12 or newer are required for development.
Ollama is used by the default classifier, local generator, and embedding
worker.

```powershell
ollama pull llama3.2:3b-instruct-q4_K_M
ollama pull phi4-mini
ollama pull all-minilm

python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

cd ui
npm install
cd ..
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.
The repository also provides `scripts/setup.ps1` and `scripts/setup.sh` for the
same initial setup.

Store optional cloud provider credentials in the OS keyring rather than the
repository:

```powershell
python -m yagami.set_key ANTHROPIC_API_KEY
python -m yagami.set_key OPENAI_API_KEY
```

Run the API and hot-reloading UI in separate terminals:

```powershell
yagami --reload
```

```powershell
cd ui
npm run dev
```

The API listens on port 8000. Vite listens on port 5173 and proxies API calls
during development.

## Verification

Run the checks CI uses before opening a pull request:

```powershell
pytest
ruff check src tests
ruff format --check src tests
mypy src
python -m evals.run_routing
python -m evals.run_containment

cd ui
npx tsc --noEmit
npm run build
```

Some evaluation commands require a running Yagami service; see the
[benchmark guide](benchmarks.md) for their setup and output formats.

## Add an OpenAI-compatible backend

Backend modules are discovered from `src/yagami/backends`. A module becomes a
backend when it exposes `build(cfg, secrets_get)`. For a compatible provider,
subclass `OpenAICompatBackend`; `groq.py` is the smallest complete example.

Every backend must declare:

- a unique `name`;
- accurate `Capability` values;
- `is_local`, which is a security boundary rather than a marketing label;
- a `Pricing` value, using zeroes only for genuinely local/free inference;
- bounded, non-crashing `generate`, `health`, and `close` behavior.

A backend marked local must validate that its transport cannot reach a remote
host. Missing optional credentials or files should make `build` return `None`
instead of preventing Yagami from starting. Runtime provider failures should
be emitted as error chunks.

Add registry, adapter, health, configuration, and security-boundary tests for
every backend. If it is selectable in the browser, add it to both default and
profile selectors in `SettingsModal.tsx`.

## Add a skill

Skill modules in `src/yagami/skills` expose a zero-argument `build()` and
return an object matching the `Skill` protocol. A skill declares its input
schema, network use, and honest `sensitivity_ceiling`.

Skills must not raise into a chat turn. Catch operational failures and return
`SkillResult(ok=False, error=...)`. Networked or third-party skills should use
a conservative sensitivity ceiling unless their data handling has been
explicitly designed and tested for sensitive context.

The implementation examples are in
[`src/yagami/backends`](https://github.com/MatthewTracy/yagami/tree/main/src/yagami/backends)
and [`src/yagami/skills`](https://github.com/MatthewTracy/yagami/tree/main/src/yagami/skills).
