"""Routing-decision eval. Loads evals/fixtures/routing.jsonl, fires each prompt
at a running Yagami WS, captures the routing chunk, and compares against
expectations.

Usage:
    python -m evals.run_routing                  # default ws://127.0.0.1:8000
    python -m evals.run_routing --url ws://...
    python -m evals.run_routing --category phi_medical_explicit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from websockets.asyncio.client import connect

FIXTURES = Path(__file__).parent / "fixtures" / "routing.jsonl"


@dataclass
class Result:
    case: dict
    actual_backend: str
    actual_intent: str
    actual_sensitivity: str
    actual_is_local: bool
    error: str | None
    failures: list[str]

    @property
    def passed(self) -> bool:
        return self.error is None and not self.failures


def load_fixtures(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(json.loads(s))
    return out


async def one_case(url: str, case: dict) -> Result:
    failures: list[str] = []
    try:
        async with connect(url, max_size=16 * 1024 * 1024) as ws:
            backend = intent = sensitivity = ""
            is_local = False
            for _ in range(20):
                msg = await asyncio.wait_for(ws.recv(), timeout=60)
                data = json.loads(msg)
                t = data.get("type")
                if t == "session":
                    await ws.send(json.dumps({"content": case["prompt"]}))
                elif t == "routing":
                    backend = data["backend"]
                    is_local = data["is_local"]
                    cls = data.get("classification") or {}
                    intent = cls.get("intent", "")
                    sensitivity = cls.get("sensitivity", "")
                    await ws.send(json.dumps({"type": "cancel"}))
                elif t in ("done", "error"):
                    break
    except Exception as e:
        return Result(case=case, actual_backend="", actual_intent="", actual_sensitivity="",
                      actual_is_local=False, error=str(e), failures=[])

    if "expected_backend" in case and backend != case["expected_backend"]:
        failures.append(f"backend: expected {case['expected_backend']!r} got {backend!r}")
    if "expected_intent" in case and intent != case["expected_intent"]:
        failures.append(f"intent: expected {case['expected_intent']!r} got {intent!r}")
    if "expected_sensitivity" in case and sensitivity != case["expected_sensitivity"]:
        failures.append(f"sensitivity: expected {case['expected_sensitivity']!r} got {sensitivity!r}")
    if case.get("must_be_local") and not is_local:
        failures.append(f"must_be_local=true but backend {backend!r} is_local=False")

    return Result(case=case, actual_backend=backend, actual_intent=intent,
                  actual_sensitivity=sensitivity, actual_is_local=is_local,
                  error=None, failures=failures)


def _color(ok: bool, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return ("\033[32m" if ok else "\033[31m") + text + "\033[0m"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws/chat")
    ap.add_argument("--category", help="Only run cases matching this category")
    ap.add_argument("--out", default=None, help="Optional JSON output path")
    args = ap.parse_args()

    cases = load_fixtures(FIXTURES)
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
    if not cases:
        print("no cases to run", file=sys.stderr)
        return 1

    results: list[Result] = []
    for c in cases:
        r = await one_case(args.url, c)
        results.append(r)
        mark = _color(r.passed, "PASS" if r.passed else "FAIL")
        print(f"{mark}  [{c.get('category','-'):22s}] {c['prompt'][:70]}")
        if r.error:
            print(f"        ERROR: {r.error}")
        for f in r.failures:
            print(f"        -> {f}")

    by_cat: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by_cat[r.case.get("category", "-")].append(r)

    print()
    print(f"{'category':24s} {'pass':>5s}/{'total':<5s}  {'rate':>6s}")
    for cat in sorted(by_cat.keys()):
        rs = by_cat[cat]
        p = sum(1 for r in rs if r.passed)
        rate = 100.0 * p / len(rs)
        line = f"{cat:24s} {p:>5d}/{len(rs):<5d}  {rate:>5.1f}%"
        print(_color(p == len(rs), line))

    total_pass = sum(1 for r in results if r.passed)
    overall_ok = total_pass == len(results)
    print()
    print(_color(overall_ok, f"OVERALL  {total_pass}/{len(results)}  ({100.0*total_pass/len(results):.1f}%)"))

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                [
                    {
                        "case": r.case,
                        "backend": r.actual_backend,
                        "intent": r.actual_intent,
                        "sensitivity": r.actual_sensitivity,
                        "is_local": r.actual_is_local,
                        "error": r.error,
                        "failures": r.failures,
                        "passed": r.passed,
                    }
                    for r in results
                ],
                indent=2,
            ),
            encoding="utf-8",
        )

    return 0 if overall_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
