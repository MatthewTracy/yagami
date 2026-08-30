# Reproducible evaluation

Yagami ships deterministic routing, refusal, and containment corpora under
`evals/fixtures`. The benchmark runners call the same public API used in
production and can emit JSON plus JUnit XML for CI.

The suite needs a real local classifier plus one registered external backend so
it can prove both containment and benign non-containment. It calls only policy
preview; it does **not** call provider generation. For a development run, a
non-secret placeholder can therefore register the Anthropic route:

```bash
python -m pip install "yagami[providers]"
ANTHROPIC_API_KEY=preview-only-not-a-real-key yagami serve
python evals/run_containment.py \
  --url http://127.0.0.1:8000 \
  --cloud-model anthropic \
  --out containment-results.json \
  --junit containment-results.xml
```

On PowerShell, set `$env:ANTHROPIC_API_KEY='preview-only-not-a-real-key'` before
starting Yagami. Never reuse this placeholder for a generation request.

The containment suite covers direct identifiers, clinical context, secrets,
conversation history, retrieved-document contamination, tool approvals, and
benign controls. A release should report per-category recall, benign false
positive rate, policy-preview latency, and the exact commit/configuration.

Generate the public JSON and Markdown report with
`python -m evals.generate_report`. The report schema lives at
`benchmarks/report.schema.json`; published results are stored under a versioned
`benchmarks/vX.Y.Z/` directory. A valid report discloses:

- The commit and cryptographic hash of the fixture corpus.
- Operating system, hardware, Python version, and model names.
- Detector set and material threshold/configuration choices.
- Overall accuracy, attack recall, benign false-positive rate, explicit
  prompt-injection detection, and policy-preview p50/p95 latency.

Counts are not a security claim. Add organization-specific failure cases to a
private fixture set and require a clean run before policy or model promotion.

## Published results

No result is currently presented as a signed-release benchmark. The fixture
semantics and tool-schema preview path changed after `v0.7.3`; attaching those
development results to the older tag would be misleading. The first public
versioned report will accompany the next signed release and will be generated
from that exact tag, model, configuration, and disclosed hardware.
