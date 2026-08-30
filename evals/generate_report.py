"""Create a public, aggregate benchmark report from containment results."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

FIXTURES = Path(__file__).parent / "fixtures" / "containment.jsonl"
SCHEMA = Path(__file__).parents[1] / "benchmarks" / "report.schema.json"


def _rate(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 3) if denominator else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(
    rows: list[dict[str, Any]],
    *,
    release: str,
    commit: str,
    model: str,
    detectors: str,
    hardware: str,
    configuration: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    attacks = [row for row in rows if row["case"].get("category") == "prompt-injection"]
    sensitive = [
        row
        for row in rows
        if row["case"].get("category")
        in {"pii-identifiers", "clinical", "secrets", "rag-contamination"}
    ]
    benign = [row for row in rows if row["case"].get("category") == "benign-controls"]
    injection = [row for row in rows if row["case"].get("category") == "prompt-injection"]
    durations = [
        float(row["duration_ms"])
        for row in rows
        if row.get("duration_ms") is not None and row.get("error") is None
    ]
    category_totals = Counter(row["case"].get("category", "uncategorized") for row in rows)
    category_passed = Counter(
        row["case"].get("category", "uncategorized") for row in rows if row.get("passed")
    )
    injection_detected = sum(
        bool(
            ((row.get("response") or {}).get("policy") or {})
            .get("context_risk", {})
            .get("untrusted_prompt_injection")
        )
        for row in injection
    )
    return {
        "schema_version": "1.0.0",
        "release": release,
        "commit": commit,
        "generated_at": generated_at
        or datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        "suite": {
            "name": "containment",
            "fixture": str(FIXTURES.as_posix()),
            "fixture_hash": _sha256(FIXTURES),
            "cases": len(rows),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "hardware": hardware,
            "model": model,
            "detectors": detectors,
            "configuration": configuration,
        },
        "metrics": {
            "passed": sum(bool(row.get("passed")) for row in rows),
            "total": len(rows),
            "accuracy_percent": _rate(sum(bool(row.get("passed")) for row in rows), len(rows)),
            "sensitive_containment_percent": _rate(
                sum(bool(row.get("passed")) for row in sensitive), len(sensitive)
            ),
            "attack_recall_percent": _rate(
                sum(bool(row.get("passed")) for row in attacks), len(attacks)
            ),
            "benign_false_positive_percent": _rate(
                sum(not bool(row.get("passed")) for row in benign), len(benign)
            ),
            "injection_detection_percent": _rate(injection_detected, len(injection)),
            "latency_ms": {
                "p50": _percentile(durations, 0.50),
                "p95": _percentile(durations, 0.95),
                "maximum": round(max(durations), 3) if durations else 0.0,
            },
        },
        "categories": {
            category: {
                "passed": category_passed[category],
                "total": total,
                "accuracy_percent": _rate(category_passed[category], total),
            }
            for category, total in sorted(category_totals.items())
        },
        "disclosure": (
            "Synthetic public fixtures only. Results describe this exact commit, "
            "configuration, model, detector set, and hardware; they are not a "
            "general security or compliance guarantee."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    latency = metrics["latency_ms"]
    lines = [
        f"# Yagami {report['release']} containment benchmark",
        "",
        f"- Commit: `{report['commit']}`",
        f"- Generated: {report['generated_at']}",
        f"- Fixture hash: `{report['suite']['fixture_hash']}`",
        f"- Model: {report['environment']['model']}",
        f"- Detectors: {report['environment']['detectors']}",
        f"- Hardware: {report['environment']['hardware']}",
        f"- Configuration: {report['environment']['configuration']}",
        "",
        "## Results",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Overall accuracy | {metrics['accuracy_percent']:.3f}% |",
        f"| Sensitive-context containment | {metrics['sensitive_containment_percent']:.3f}% |",
        f"| Attack recall | {metrics['attack_recall_percent']:.3f}% |",
        (f"| Benign false-positive rate | {metrics['benign_false_positive_percent']:.3f}% |"),
        (f"| Prompt-injection detection | {metrics['injection_detection_percent']:.3f}% |"),
        f"| Policy-preview latency p50 | {latency['p50']:.3f} ms |",
        f"| Policy-preview latency p95 | {latency['p95']:.3f} ms |",
        "",
        "## Categories",
        "",
        "| Category | Passed | Total | Accuracy |",
        "|---|---:|---:|---:|",
    ]
    for category, result in report["categories"].items():
        lines.append(
            f"| {category} | {result['passed']} | {result['total']} | "
            f"{result['accuracy_percent']:.3f}% |"
        )
    lines.extend(["", f"> {report['disclosure']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--release", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--detectors", required=True)
    parser.add_argument("--hardware", required=True)
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))
    report = build_report(
        rows,
        release=args.release,
        commit=args.commit,
        model=args.model,
        detectors=args.detectors,
        hardware=args.hardware,
        configuration=args.configuration,
    )
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(report, schema)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(f"wrote {args.json} and {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
