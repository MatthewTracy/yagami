# Reproducible evaluation

Yagami ships deterministic routing, refusal, and containment corpora under
`evals/fixtures`. The benchmark runners call the same public API used in
production and can emit JSON plus JUnit XML for CI.

```bash
yagami demo

python evals/run_containment.py \
  --url http://127.0.0.1:8000 \
  --out containment-results.json \
  --junit containment-results.xml
```

The containment suite covers direct identifiers, clinical context, secrets,
conversation history, retrieved-document contamination, tool approvals, and
benign controls. A release should report per-category recall, benign false
positive rate, policy-preview latency, and the exact commit/configuration.

Counts are not a security claim. Add organization-specific failure cases to a
private fixture set and require a clean run before policy or model promotion.
