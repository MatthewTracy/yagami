from __future__ import annotations

from evals.run_containment import FIXTURES, load_fixtures
from yagami.governance import inspect_output
from yagami.router.schema import Sensitivity


def test_containment_corpus_has_unique_cross_category_cases() -> None:
    cases = load_fixtures(FIXTURES)
    assert len(cases) >= 12
    assert len({case["id"] for case in cases}) == len(cases)
    assert {
        "pii-identifiers",
        "clinical",
        "secrets",
        "rag-contamination",
        "tool-governance",
        "benign-controls",
    }.issubset({case["category"] for case in cases})


def test_output_inspection_labels_identifiers_and_secrets_without_content() -> None:
    phi = inspect_output("Contact jane@example.com")
    secret = inspect_output("Rotate AKIAIOSFODNN7EXAMPLE")
    public = inspect_output("The sky appears blue because of Rayleigh scattering.")

    assert phi.sensitivity == Sensitivity.PHI
    assert phi.summary()["entity_counts"] == {"EMAIL": 1}
    assert secret.sensitivity == Sensitivity.SECRET
    assert secret.summary()["entity_counts"] == {"AWS_KEY": 1}
    assert public.sensitivity == Sensitivity.NONE
    assert public.summary()["entity_counts"] == {}
