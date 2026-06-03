"""Refusal-detection eval. Sends PHI prompts end-to-end and grades the model's
ACTUAL reply for engagement vs refusal. Catches regressions in the PHI system
prompt (e.g. the model that "describes a unicorn" when asked to generate one,
or refuses to summarize a clinical note).

Slower than run_routing.py - actually generates tokens. ~30-120 sec/case.

Usage:
    python -m evals.run_refusals
    python -m evals.run_refusals --url ws://...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from websockets.asyncio.client import connect

FIXTURES = Path(__file__).parent / "fixtures" / "refusals.jsonl"

REFUSAL_PHRASES = [
    "i can't provide medical advice",
    "i cannot provide medical advice",
    "consult a healthcare professional",
    "consult a qualified healthcare",
    "i'm not capable of",
    "i don't have the capability",
    "i'm a conversational ai",
    "i'm a large language model",
    "i'm not able to access",
    "as an ai language model",
    "i can't directly review",
    "i cannot directly review",
    "i can't access",
    "i cannot access",
]


@dataclass
class Result:
    prompt: str
    reply: str
    backend: str
    is_local: bool
    refusal_hits: list[str]
    engagement_hits: list[str]
    min_engagement_hits: int
    error: str | None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        if not self.is_local:
            return False
        if self.refusal_hits:
            return False
        if len(self.engagement_hits) < self.min_engagement_hits:
            return False
        return True


def load_fixtures(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(json.loads(s))
    return out


async def one_case(url: str, case: dict) -> Result:
    backend = ""
    is_local = False
    pieces: list[str] = []
    try:
        async with connect(url, max_size=4 * 1024 * 1024) as ws:
            for _ in range(5000):
                msg = await asyncio.wait_for(ws.recv(), timeout=120)
                data = json.loads(msg)
                t = data.get("type")
                if t == "session":
                    await ws.send(json.dumps({"content": case["prompt"]}))
                elif t == "routing":
                    backend = data["backend"]
                    is_local = data["is_local"]
                elif t == "text":
                    pieces.append(data["content"])
                elif t in ("done", "error"):
                    break
    except Exception as e:
        return Result(
            prompt=case["prompt"],
            reply="",
            backend=backend,
            is_local=is_local,
            refusal_hits=[],
            engagement_hits=[],
            min_engagement_hits=case.get("min_engagement_hits", 1),
            error=str(e),
        )

    reply = "".join(pieces).lower()
    refusal_hits = [p for p in REFUSAL_PHRASES if p in reply]
    expect = [w.lower() for w in case.get("expect_engagement", [])]
    engagement_hits = [w for w in expect if w in reply]
    return Result(
        prompt=case["prompt"],
        reply="".join(pieces),
        backend=backend,
        is_local=is_local,
        refusal_hits=refusal_hits,
        engagement_hits=engagement_hits,
        min_engagement_hits=case.get("min_engagement_hits", 1),
        error=None,
    )


def _color(ok: bool, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return ("\033[32m" if ok else "\033[31m") + text + "\033[0m"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws/chat")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cases = load_fixtures(FIXTURES)
    results: list[Result] = []
    for c in cases:
        r = await one_case(args.url, c)
        results.append(r)
        mark = _color(r.passed, "PASS" if r.passed else "FAIL")
        snippet = (r.reply or "")[:80].replace("\n", " ")
        print(f"{mark}  {c['prompt'][:70]}")
        print(
            f"        backend={r.backend} local={r.is_local}  "
            f"engagement={len(r.engagement_hits)}/{r.min_engagement_hits}  "
            f"refusals={len(r.refusal_hits)}"
        )
        print(f"        reply: {snippet}…")
        if r.refusal_hits:
            print(f"        refusal phrases hit: {r.refusal_hits}")

    passed = sum(1 for r in results if r.passed)
    overall_ok = passed == len(results)
    print()
    print(
        _color(
            overall_ok, f"OVERALL  {passed}/{len(results)}  ({100.0 * passed / len(results):.1f}%)"
        )
    )

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                [
                    {
                        "prompt": r.prompt,
                        "backend": r.backend,
                        "is_local": r.is_local,
                        "refusal_hits": r.refusal_hits,
                        "engagement_hits": r.engagement_hits,
                        "min_engagement_hits": r.min_engagement_hits,
                        "passed": r.passed,
                        "error": r.error,
                        "reply": r.reply,
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
