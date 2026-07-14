"""Gateway containment benchmark using policy preview (no provider generation calls).

Run against a configured Yagami process so the benchmark exercises the same
local classifier, project policy, and backend inventory as production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "containment.jsonl"


@dataclass
class Result:
    case: dict
    status_code: int
    response: dict
    failures: list[str]
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and not self.failures


def load_fixtures(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


async def one_case(
    client: httpx.AsyncClient,
    case: dict,
    *,
    cloud_model: str,
) -> Result:
    metadata = dict(case.get("metadata", {}))
    model = cloud_model if case.get("model") == "$cloud" else case.get("model", "yagami-auto")
    messages = case.get("messages") or [{"role": "user", "content": case["prompt"]}]
    payload = {
        "model": model,
        "messages": messages,
        "metadata": metadata,
        "tools": case.get("tools"),
    }
    try:
        response = await client.post("/v1/policy/preview", json=payload)
        body = response.json()
    except Exception as exc:  # noqa: BLE001 - benchmark reports transport failures per case
        return Result(case=case, status_code=0, response={}, failures=[], error=str(exc))

    failures: list[str] = []
    if response.status_code != 200:
        failures.append(f"HTTP {response.status_code}: {body}")
        return Result(case, response.status_code, body, failures)

    policy = body.get("policy", {})
    if "expected_allowed" in case and body.get("allowed") is not case["expected_allowed"]:
        failures.append(
            f"allowed expected {case['expected_allowed']!r}, got {body.get('allowed')!r}"
        )
    if case.get("must_be_local") and body.get("is_local") is not True:
        failures.append(f"containment failure: backend {body.get('backend')!r} is not local")
    expected_sensitivity = case.get("expected_sensitivity")
    if expected_sensitivity and policy.get("effective_sensitivity") != expected_sensitivity:
        failures.append(
            "sensitivity expected "
            f"{expected_sensitivity!r}, got {policy.get('effective_sensitivity')!r}"
        )
    required_tool = case.get("expected_approval_tool")
    if required_tool and required_tool not in policy.get("require_approval_for_tools", []):
        failures.append(f"expected approval requirement for {required_tool!r}")
    expected_cloud = case.get("expected_cloud")
    if expected_cloud is True and body.get("is_local") is not False:
        failures.append(f"false positive containment: expected cloud, got {body.get('backend')!r}")
    expected_context_risk = case.get("expected_context_risk")
    actual_context_risk = bool((policy.get("context_risk") or {}).get("untrusted_prompt_injection"))
    if expected_context_risk is not None and actual_context_risk is not expected_context_risk:
        failures.append(
            f"context risk expected {expected_context_risk!r}, got {actual_context_risk!r}"
        )
    return Result(case, response.status_code, body, failures)


def write_junit(path: Path, results: list[Result]) -> None:
    suite = ET.Element(
        "testsuite",
        name="yagami-containment",
        tests=str(len(results)),
        failures=str(sum(not result.passed for result in results)),
    )
    for result in results:
        case = ET.SubElement(
            suite,
            "testcase",
            classname=f"containment.{result.case.get('category', 'uncategorized')}",
            name=result.case["id"],
        )
        if not result.passed:
            failure = ET.SubElement(case, "failure", message="; ".join(result.failures))
            failure.text = result.error or json.dumps(result.response, sort_keys=True)
    path.write_text(ET.tostring(suite, encoding="unicode"), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=os.getenv("YAGAMI_API_KEY", ""))
    parser.add_argument("--cloud-model", default="anthropic")
    parser.add_argument("--category")
    parser.add_argument("--out")
    parser.add_argument("--junit")
    args = parser.parse_args()

    cases = load_fixtures(FIXTURES)
    if args.category:
        cases = [case for case in cases if case.get("category") == args.category]
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    async with httpx.AsyncClient(
        base_url=args.url.rstrip("/"),
        headers=headers,
        timeout=90,
    ) as client:
        results = [await one_case(client, case, cloud_model=args.cloud_model) for case in cases]

    by_category: dict[str, list[Result]] = defaultdict(list)
    for result in results:
        by_category[result.case.get("category", "uncategorized")].append(result)
        marker = "PASS" if result.passed else "FAIL"
        print(f"{marker:4s} [{result.case.get('category', '-'):20s}] {result.case['id']}")
        for failure in result.failures:
            print(f"     -> {failure}")
        if result.error:
            print(f"     -> {result.error}")

    print("\ncategory                 pass/total")
    for category, category_results in sorted(by_category.items()):
        passed = sum(result.passed for result in category_results)
        print(f"{category:24s} {passed:>3d}/{len(category_results):<3d}")
    passed = sum(result.passed for result in results)
    print(f"\nOVERALL {passed}/{len(results)} ({100 * passed / max(len(results), 1):.1f}%)")

    serializable = [
        {
            "case": result.case,
            "status_code": result.status_code,
            "response": result.response,
            "failures": result.failures,
            "error": result.error,
            "passed": result.passed,
        }
        for result in results
    ]
    if args.out:
        Path(args.out).write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    if args.junit:
        write_junit(Path(args.junit), results)
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
