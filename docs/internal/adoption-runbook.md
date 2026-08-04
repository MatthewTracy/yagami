# Adoption and publication runbook

Complete these steps from the maintainer accounts. Do not put registry tokens
in the repository; the prepared workflow uses trusted publishing and OIDC.

## Before the release pull request

1. Choose a new minor version rather than republishing `0.7.3`. Moving provider,
   PDF, and keyring dependencies to extras changes installation behavior, so
   `0.8.0` is the conservative pre-1.0 choice.
2. Confirm each named package is either public or still labeled “Pending first
   publication” in `integrations/README.md`.
3. Repeat the clean-wheel, local-tool, containment, docs, container, UI, and
   adapter checks from the release checklist.
4. Generate the benchmark from the exact release commit. Commit its versioned
   report only after schema validation and human review of the environment and
   limitations fields.

## Registry ownership

Create PyPI pending trusted publishers for all three adapters using:

- Owner: `MatthewTracy`
- Repository: `yagami`
- Workflow: `release.yml`
- Environment: `pypi`
- Projects: `langchain-yagami`, `llama-index-llms-yagami`, and
  `llama-index-embeddings-yagami`

Reserve the npm `@yagami` organization, create the
`@yagami/ai-sdk-provider` package/trusted publisher for `release.yml` and the
`release` environment, and require two-factor authentication on the owner
account.

Only after those namespaces are controlled, set repository variables:

- `ADAPTER_PYPI_PUBLISH_ENABLED=true`
- `NPM_PUBLISH_ENABLED=true`

Core PyPI, GHCR, Helm, and MCP publication switches should remain as currently
verified. The release workflow skips immutable versions that already exist and
smoke-installs public artifacts before promoting the mutable container tag.

## GitHub discovery settings

In repository settings:

1. Set the social preview to `docs/assets/og.png`.
2. Use the description: “Self-hosted LLM gateway and AI context firewall for
   PII/PHI containment, prompt-injection defense, and governed agent tools.”
3. Add focused topics: `llm-gateway`, `ai-security`, `ai-guardrails`,
   `prompt-injection`, `pii`, `mcp`, `ollama`, `self-hosted`, `rag-security`,
   and `data-governance`.
4. Confirm branch protection/rulesets still require CI, CodeQL, dependency
   review, and signed release checks.
5. Uploading the social preview is separate from the MkDocs Open Graph tags;
   both are required for consistent cards.

Enroll the repository in the
[OpenSSF Best Practices](https://www.bestpractices.dev/) program before adding
its badge. The Scorecard workflow already covers a different set of checks.

## After publication

1. Install every package from its public registry in a clean environment and
   update `integrations/README.md` from Pending to Published only after success.
2. Submit upstream catalog entries using `launch/upstream-submissions.md`.
3. Recruit five design partners directly before broad promotion; seek at least
   three weekly deployments and record failure cases, time-to-first-policy,
   and retained usage rather than relying on download counts.
4. Post the launch drafts manually, one community at a time, and answer with
   the comparison, threat-model, and benchmark links rather than broad claims.
5. Recheck unique repository visitors, quickstart completions, public-package
   installs, and retained pilots after two weeks. Treat raw container pulls as
   CI noise unless tied to a verified activation.

