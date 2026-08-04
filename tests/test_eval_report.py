from __future__ import annotations

from evals.generate_report import build_report, render_markdown


def _row(category: str, passed: bool, duration: float, *, detected: bool = False):
    return {
        "case": {"id": f"{category}-{duration}", "category": category},
        "status_code": 200,
        "response": {"policy": {"context_risk": {"untrusted_prompt_injection": detected}}},
        "failures": [] if passed else ["failed"],
        "error": None,
        "passed": passed,
        "duration_ms": duration,
    }


def test_build_report_discloses_accuracy_false_positives_detection_and_latency():
    rows = [
        _row("secrets", True, 5),
        _row("prompt-injection", True, 10, detected=True),
        _row("benign-controls", True, 20),
        _row("benign-controls", False, 40),
    ]
    report = build_report(
        rows,
        release="v0.7.0",
        commit="abcdef0123456789",
        model="test-model",
        detectors="built-in",
        hardware="test-host",
        configuration="config hash",
        generated_at="2026-07-26T00:00:00+00:00",
    )
    assert report["metrics"]["accuracy_percent"] == 75
    assert report["metrics"]["sensitive_containment_percent"] == 100
    assert report["metrics"]["attack_recall_percent"] == 100
    assert report["metrics"]["benign_false_positive_percent"] == 50
    assert report["metrics"]["injection_detection_percent"] == 100
    assert report["metrics"]["latency_ms"] == {
        "p50": 10.0,
        "p95": 40.0,
        "maximum": 40.0,
    }
    markdown = render_markdown(report)
    assert "Fixture hash" in markdown
    assert "Sensitive-context containment" in markdown
    assert "universal security" not in markdown
    assert "general security or compliance guarantee" in markdown
