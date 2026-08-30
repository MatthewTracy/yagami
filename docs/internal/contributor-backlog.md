# Contributor issue backlog

These are issue-ready drafts, not promises that the work has shipped. Before
filing one, confirm it is still relevant and add the appropriate milestone.
Security-sensitive reports still belong in the private reporting channel
described by `SECURITY.md`.

## Good first issues

### 1. Add a `doctor --json` output mode

**Labels:** `good first issue`, `help wanted`, `cli`

**Files:** `src/yagami/doctor.py`, `src/yagami/cli.py`, `tests/test_doctor.py`

**Acceptance:** expose the same checks as the human report in a stable JSON
envelope; keep secrets and endpoint credentials out; add schema-focused tests
and CLI documentation.

### 2. Document a rootless Podman quickstart

**Labels:** `good first issue`, `documentation`, `containers`

**Files:** `docs/deployment.md`, `README.md`

**Acceptance:** verify commands on current Podman, bind only to loopback by
default, persist data without root-owned host files, and document SELinux
volume labels where needed.

### 3. Add copyable PydanticAI example tests

**Labels:** `good first issue`, `help wanted`, `integration`

**Files:** `examples/frameworks/`, `tests/`

**Acceptance:** one minimal example uses Yagami's OpenAI-compatible endpoint;
CI exercises it with a fake backend; evidence metadata is shown without model
content.

### 4. Improve policy-denial troubleshooting links

**Labels:** `good first issue`, `documentation`, `ui`

**Files:** `ui/src/`, `docs/policies.md`

**Acceptance:** denial reason codes link to an explanation and safe next step;
the UI never displays internal exception details; keyboard navigation and
screen-reader labels are tested.

### 5. Add a configuration reference generator

**Labels:** `good first issue`, `help wanted`, `documentation`

**Files:** `src/yagami/config.py`, `scripts/`, `docs/configuration.md`

**Acceptance:** generate names, defaults, types, and safe descriptions from
the settings model; CI fails on drift; secret defaults and values are never
rendered.

### 6. Add a health-check example for Compose

**Labels:** `good first issue`, `containers`

**Files:** `compose.demo.yaml`, `compose.yaml`, `docs/deployment.md`

**Acceptance:** use the unauthenticated liveness endpoint only for liveness;
document readiness separately; `docker compose config` and the container smoke
test pass.

## Help wanted

### 7. Build a durable Responses job worker

**Labels:** `help wanted`, `reliability`, `database`

**Files:** `src/yagami/api/openai_compat.py`, `src/yagami/storage/`, `alembic/`

**Acceptance:** persist a content-safe execution envelope; claim work with a
lease; recover abandoned jobs after process death; support cancellation and
multi-replica execution; add migration, restart, contention, and privacy tests.

### 8. Split Responses lifecycle code from the compatibility API

**Labels:** `help wanted`, `refactor`

**Files:** `src/yagami/api/openai_compat.py`, `src/yagami/api/`

**Acceptance:** move lifecycle, cancellation, and event serialization behind a
small service interface; preserve routes and schemas; no behavior or coverage
regression.

### 9. Split gateway pipeline stages behind typed interfaces

**Labels:** `help wanted`, `refactor`

**Files:** `src/yagami/gateway/service.py`, `src/yagami/gateway/`

**Acceptance:** isolate classification, policy, provider execution, tool loop,
and evidence finalization; preserve ordering and fail-closed behavior; add
stage contract tests.

### 10. Validate llama.cpp tool-call chat formats

**Labels:** `help wanted`, `backend`, `local-ai`

**Files:** `src/yagami/backends/llama_cpp.py`, `tests/test_backend_adapters.py`,
`docs/integrations.md`

**Acceptance:** test at least two tool-capable model/chat-format combinations;
document exact versions; keep tool capability disabled unless explicitly
configured; malformed calls fail closed.

### 11. Add public benchmark fixture provenance

**Labels:** `help wanted`, `evaluation`, `security`

**Files:** `evals/`, `benchmarks/`, `docs/benchmarks.md`

**Acceptance:** every released fixture records origin/license, expected threat
class, and limitations; report generation validates provenance; no customer or
private prompt data is included.

### 12. Add multilingual containment fixtures

**Labels:** `help wanted`, `evaluation`, `security`

**Files:** `evals/fixtures/containment.jsonl`, `tests/test_containment.py`

**Acceptance:** cover at least five language/script families with benign and
adversarial pairs; publish per-slice metrics and limitations; avoid claiming
language support from synthetic fixtures alone.

### 13. Add MCP schema-drift compatibility corpus

**Labels:** `help wanted`, `mcp`, `security`

**Files:** `tests/`, `evals/`, `docs/integrations.md`

**Acceptance:** cover compatible additions, breaking changes, malicious
description changes, and output-schema drift; verify pin, warn, and deny modes;
evidence contains hashes and reason codes but no arguments/results.

### 14. Add automated accessibility checks for the documentation site

**Labels:** `help wanted`, `documentation`, `accessibility`

**Files:** `docs/`, `mkdocs.yml`, `.github/workflows/ci.yml`

**Acceptance:** build the site and scan representative pages with axe using
WCAG 2.2 AA tags; test keyboard landmarks and reduced-motion behavior; keep
the existing no-data tour free of analytics and external runtime calls.

### 15. Add release artifact installation matrix

**Labels:** `help wanted`, `release`, `ci`

**Files:** `.github/workflows/release.yml`, `scripts/`, `integrations/`

**Acceptance:** after publication, install every enabled immutable artifact
from its public registry on supported runtimes; skip already-published versions
on rerun; promote mutable tags only after required smoke tests pass.

### 16. Add retention cleanup observability

**Labels:** `help wanted`, `observability`, `privacy`

**Files:** `src/yagami/retention.py`, `src/yagami/observability/`, `docs/slo.md`

**Acceptance:** export content-free counts, durations, and safe error codes;
alert on cleanup lag without record identifiers; test logs and traces for
content leakage.
