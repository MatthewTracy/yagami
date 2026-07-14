# Contributing to Yagami

Thanks for taking a look. Yagami is early (alpha), so the fastest way to have
impact is usually a small, focused PR rather than a large one.

## Before you start

- **Small fix / new skill / new backend?** Just open a PR.
- **Anything that touches the routing schema, the storage migrations, or adds
  a new backend/skill category?** Open an issue first so we can agree on the
  shape before you write code. `RoutingPolicy.decide()` in
  [`src/yagami/router/policy.py`](src/yagami/router/policy.py) is the single
  chokepoint every routing decision goes through — changes there ripple
  everywhere, so they're worth discussing up front.
- Looking for a first PR? Check issues labeled
  [`good first issue`](https://github.com/MatthewTracy/yagami/labels/good%20first%20issue).
  The Roadmap section in [`README.md`](README.md) also lists what's planned
  next — a new skill (one file in `src/yagami/skills/` plus a test) is
  usually the lowest-friction contribution.

## Dev setup

See the [Quickstart](README.md#quickstart) in the README. tl;dr: Python
3.11+, Node 22.12+, Ollama with three models pulled.

## Adding a backend or skill

Both are filesystem-discovered — drop a file in `src/yagami/backends/` or
`src/yagami/skills/` that exposes a `build(...)` function, and the registry
picks it up on boot. No edit to `main.py` or the registry needed. Full
worked examples (a Mistral backend, a clipboard skill) are in the README's
["Adding your own backend"](README.md#adding-your-own-backend) and
["Adding your own skill"](README.md#adding-your-own-skill) sections — read
those before starting; this file won't duplicate them.

A few rules that aren't obvious from the protocol types alone:

- **Skills must never raise.** Catch exceptions inside `run()` and return
  `SkillResult(ok=False, error=str(exc))`. A skill exception must not be able
  to take down a chat turn.
- **Set `sensitivity_ceiling` honestly.** If a skill touches the network or
  an external service, it should refuse PHI/secret sessions unless you have
  a specific reason it's safe (see `web.fetch` in
  [`src/yagami/skills/web_fetch.py`](src/yagami/skills/web_fetch.py) for the
  pattern).
- **Backends should degrade, not crash.** A missing API key or unreachable
  local model should make `build()` return `None` (backend just doesn't show
  up), not raise. Runtime errors during `generate()` should surface as an
  `error` chunk, not an unhandled exception — see
  [`src/yagami/backends/retry.py`](src/yagami/backends/retry.py) for the
  retry-on-transient-error pattern used by the cloud backends.

## Before opening a PR

Run all of these — CI runs the same checks and will block on any failure:

```powershell
pytest                          # all tests must pass
ruff check src tests            # 0 warnings
ruff format --check src tests   # formatting must be clean
python -m evals.run_routing     # 48/48; if you change routing, update evals/fixtures/*.jsonl
cd ui ; npx tsc --noEmit ; npm run build
```

If you added a backend, add its key to `ALL_FAKE_SECRETS` in
[`tests/test_backend_registry.py`](tests/test_backend_registry.py) rather
than writing a bespoke test file — the generic protocol checks there
(`test_every_backend_implements_protocol`,
`test_every_backend_declares_pricing_attr`, etc.) will automatically cover
your backend once it's in that dict.

If you changed anything in `src/yagami/router/`, run
`python -m evals.run_routing` against a running `uvicorn` instance and update
`evals/fixtures/routing.jsonl` if the expected decisions changed on purpose.

## Commit messages

Plain, descriptive commit subjects are fine — prefixing with
`feat:`/`fix:`/`docs:`/`chore:` (Conventional Commits style) is appreciated
but not required. What matters more: one logical change per commit, and a
subject line that explains *why*, not just *what changed*.

## Code style

`ruff` (Python) and `tsc` + the existing component patterns (TypeScript/UI)
are the enforced bar. Beyond that, match the surrounding file — this is a
small enough codebase that consistency matters more than any individual
style preference.
