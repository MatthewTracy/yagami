# Local development and extensions

## Source setup

Python 3.11 or newer is required. For the UI toolchain, use Node.js
22.22.2+ in the 22.x line, 24.15.0+ in the 24.x line, or 26+.
These minimums match jsdom 30, which the UI tests use.
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
npm ci
npm test
npm run build
npx playwright install --with-deps chromium firefox webkit
npm run test:e2e
```

The UI suite covers reconnects, failed sends, IME input, pending uploads,
settings and deletion failures, stale responses, feedback, and privacy controls.
Coverage floors are 75% statements, 70% branches/functions, and 80% lines.
Browser checks exercise Chromium desktop/tablet/mobile, Firefox, and WebKit,
including keyboard navigation, focus restoration, wide responses, and automated
WCAG accessibility checks. These browser tests use deterministic API/socket
fixtures; live classifier containment is evaluated separately as described in
[reproducible evaluation](benchmarks.md).

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

The built-in `calc.eval` tool bounds synchronous work before evaluating an
expression: at most 4,096 characters, 256 AST nodes, and 32 levels of nesting.
Integer values, including intermediate results, are limited to 4,096 bits;
exponent magnitude is limited to 10,000 and factorial arguments to 512.
Oversized or malformed expressions return a failed `SkillResult`. Integer
powers use a conservative size estimate before allocating the result, so
some calculations close to the size limit may also be rejected. Boolean literals
are rejected rather than treated as integers.

`web.fetch` returns the exception type on fetch failures without copying the
exception message, which may contain credentials or query parameters from
the requested URL. Unknown response charsets also return a failed result.
The 15-second deadline covers the complete redirect chain and response stream.
Every redirect is checked against the HTTPS allowlist; URLs containing credentials,
control characters, or invalid ports are rejected before network access. Requests
ask for identity encoding and reject compressed responses before decoding to keep
the byte limit effective. Plain-text responses preserve angle brackets and layout.

The implementation examples are in
[`src/yagami/backends`](https://github.com/MatthewTracy/yagami/tree/main/src/yagami/backends)
and [`src/yagami/skills`](https://github.com/MatthewTracy/yagami/tree/main/src/yagami/skills).
