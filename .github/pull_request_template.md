## What does this change?

<!-- One or two sentences: what does this PR do, and why? -->

## Checklist

- [ ] `pytest` passes
- [ ] `ruff check src tests` — 0 warnings
- [ ] `ruff format --check src tests` — clean
- [ ] `python -m evals.run_routing` — 48/48 (if routing changed on purpose,
      `evals/fixtures/routing.jsonl` was updated to match)
- [ ] `cd ui && npx tsc --noEmit && npm run build` — clean (if `ui/` changed)
- [ ] New backend: added to `ALL_FAKE_SECRETS` in `tests/test_backend_registry.py`
      so the generic protocol tests cover it
- [ ] New skill: test added in `tests/test_skills.py`

## Anything reviewers should know?

<!-- Schema/migration changes, behavior changes to the PHI/secret gating,
     new dependencies, or anything else worth flagging. Delete this section
     if there's nothing unusual. -->
