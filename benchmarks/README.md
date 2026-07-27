# Published benchmark results

Each release benchmark belongs in a versioned directory:

```text
benchmarks/
  v0.7.0/
    containment.json
    containment.md
```

Only generated aggregate reports are committed. Raw results can contain
synthetic fixture prompts and remain CI artifacts. Every report identifies the
commit, fixture hash, model, detector configuration, hardware, and policy
configuration.

Do not publish a report produced with missing environment descriptions or
represent a single configuration as a universal security claim.
