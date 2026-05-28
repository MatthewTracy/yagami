from __future__ import annotations

import pytest

from yagami.router.schema import Classification, Sensitivity

PHI_FIXTURES = [
    "My SSN is 123-45-6789, please summarize this email.",
    "Patient John Doe (DOB 1962-04-12) has hypertension; what should I document?",
    "Here is my credit card 4111111111111111, draft a refund note.",
    "Call me at (415) 555-0199 to discuss the lab results.",
    "Email me at jane.patient@example.com about the biopsy.",
    "Diagnosis: stage II NSCLC. Plan a 6-cycle carbo/paclitaxel regimen.",
    "MRN 00987654, hgb 8.1, transfuse 2 units PRBCs?",
    "Insurance ID ABC123456789, prior auth needed for MRI L-spine.",
    "Pt reports SI with plan, considering 5150 hold.",
    "BP 180/115 in clinic today, headache + visual aura.",
    "BMP: Na 128, K 5.8, Cr 2.4. Adjust ACEI?",
    "HIV+ since 2019, currently undetectable on Biktarvy.",
    "Suicide attempt in 2017 by overdose; on sertraline 100mg.",
    "G3P2, currently 32w4d, blood pressure trending up.",
    "Address 742 Evergreen Terrace; deliver supplies next Tue.",
    "Hx of prostate ca with PSA 12.4 last draw.",
    "DEA# AB1234567 — please escribe oxycodone 5mg.",
    "Family hx significant for BRCA1 mutation; counsel re testing?",
    "ICD-10 F33.2 with recent psychiatric hospitalization.",
    "PHN 123456789, 02/12/1980, female, presenting with chest pain.",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", PHI_FIXTURES)
async def test_phi_never_reaches_remote_backend(make_policy, user_msg, prompt):
    policy = make_policy(Classification(sensitivity=Sensitivity.PHI))
    decision = await policy.decide(user_msg(prompt))
    assert decision.backend.is_local is True, (
        f"PHI prompt routed to remote backend {decision.backend.name!r}: {prompt[:60]}"
    )


@pytest.mark.asyncio
async def test_phi_medical_also_local(make_policy, user_msg):
    for prompt in PHI_FIXTURES:
        policy = make_policy(Classification(sensitivity=Sensitivity.PHI_MEDICAL))
        decision = await policy.decide(user_msg(prompt))
        assert decision.backend.is_local is True
