from __future__ import annotations

import httpx
import pytest

from evals.run_containment import FIXTURES, load_fixtures, one_case
from yagami.governance import inspect_output
from yagami.router.schema import Sensitivity


def test_containment_corpus_has_unique_cross_category_cases() -> None:
    cases = load_fixtures(FIXTURES)
    assert len(cases) >= 20
    assert len({case["id"] for case in cases}) == len(cases)
    assert {
        "pii-identifiers",
        "clinical",
        "secrets",
        "rag-contamination",
        "tool-governance",
        "benign-controls",
        "prompt-injection",
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


@pytest.mark.asyncio
async def test_containment_runner_accepts_an_expected_policy_refusal() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": "routing_refused", "message": "Request refused"}},
        )

    async with httpx.AsyncClient(
        base_url="http://yagami.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await one_case(
            client,
            {
                "id": "explicit-cloud-refused",
                "category": "clinical",
                "prompt": "Patient note",
                "expected_outcome": "blocked",
                "expected_error_code": "routing_refused",
            },
            cloud_model="anthropic",
        )

    assert result.passed


@pytest.mark.asyncio
async def test_containment_runner_never_counts_a_server_error_as_a_block() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": "internal_error"}})

    async with httpx.AsyncClient(
        base_url="http://yagami.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await one_case(
            client,
            {
                "id": "broken-policy-preview",
                "category": "prompt-injection",
                "prompt": "Untrusted context",
                "expected_outcome": "blocked",
            },
            cloud_model="anthropic",
        )

    assert not result.passed
    assert any("HTTP 500" in failure for failure in result.failures)


@pytest.mark.asyncio
async def test_containment_runner_rejects_a_local_case_routed_to_cloud() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "allowed": True,
                "backend": "anthropic",
                "is_local": False,
                "policy": {"effective_sensitivity": "phi_medical"},
            },
        )

    async with httpx.AsyncClient(
        base_url="http://yagami.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await one_case(
            client,
            {
                "id": "patient-data-contained",
                "category": "clinical",
                "prompt": "Patient note",
                "expected_outcome": "local",
                "expected_sensitivity": "phi_medical",
            },
            cloud_model="anthropic",
        )

    assert not result.passed
    assert any("private backend" in failure for failure in result.failures)
