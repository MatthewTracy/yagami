"""Latency smoke test for Yagami. Run the server, then:

    python scripts/perf_smoke.py [--url ws://127.0.0.1:8000/ws/chat] [--out perf.csv]

Fires a fixed bag of prompts (short non-PHI / PHI / code), measures
time-to-routing and time-to-first-token per turn, writes CSV, prints p50/p95
by category. Compare before and after a perf change to confirm the win.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

from websockets.asyncio.client import connect

SHORT_NON_PHI = [
    "hi",
    "hello",
    "what is 2+2",
    "what's the capital of France",
    "tell me a joke",
    "good morning",
    "thanks!",
    "lol",
    "who wrote hamlet",
    "what's the speed of sound",
    "convert 5 miles to km",
    "what year was Apollo 11",
    "spell encyclopedia",
    "what's a synonym for happy",
    "name three primary colors",
    "what's pi to 3 places",
    "is the sky blue",
    "what's 100 / 4",
    "how many continents",
    "longest river in the world",
]

PHI_PROMPTS = [
    "Patient Jane Doe DOB 1962-04-12 has HTN, T2DM, prior MI 2019. BP 158/94, BNP 612, A1c 8.1.",
    "MRN 00987654, hgb 8.1, transfuse 2 units PRBCs?",
    "My SSN is 123-45-6789, please help me file paperwork.",
    "Pt reports SI with plan, considering 5150 hold.",
    "Diagnosis: stage II NSCLC. Plan a 6-cycle carbo/paclitaxel regimen.",
    "BP 180/115 in clinic today, headache + visual aura.",
]

CODE_PROMPTS = [
    "write a python function that returns the nth fibonacci number",
    "what does `await` do in javascript",
    "fix this bug: def add(a, b): return a - b",
    "explain `console.log` in node",
]


CATEGORIES: list[tuple[str, list[str]]] = [
    ("short_non_phi", SHORT_NON_PHI),
    ("phi", PHI_PROMPTS),
    ("code", CODE_PROMPTS),
]


async def one_turn(url: str, prompt: str) -> dict:
    t_send = None
    t_route = None
    t_first_token = None
    t_done = None
    backend = ""
    source = ""

    async with connect(url, max_size=16 * 1024 * 1024) as ws:
        for _ in range(2000):
            msg = await asyncio.wait_for(ws.recv(), timeout=120)
            data = json.loads(msg)
            t = data.get("type")
            if t == "session":
                t_send = time.perf_counter()
                await ws.send(json.dumps({"content": prompt}))
            elif t == "routing":
                t_route = time.perf_counter()
                backend = data["backend"]
                source = (data.get("classification") or {}).get("source", "")
            elif t in ("text", "image_url") and t_first_token is None:
                t_first_token = time.perf_counter()
            elif t == "done":
                t_done = time.perf_counter()
                break
            elif t == "error":
                t_done = time.perf_counter()
                break

    if t_send is None or t_route is None:
        return {
            "backend": backend,
            "source": source,
            "route_ms": None,
            "ttft_ms": None,
            "total_ms": None,
        }
    return {
        "backend": backend,
        "source": source,
        "route_ms": int((t_route - t_send) * 1000),
        "ttft_ms": int((t_first_token - t_route) * 1000) if t_first_token else None,
        "total_ms": int((t_done - t_send) * 1000) if t_done else None,
    }


def pct(values: list[int], p: float) -> int | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    vals.sort()
    idx = max(0, min(len(vals) - 1, int(len(vals) * p / 100)))
    return vals[idx]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws/chat")
    ap.add_argument("--out", default=f"perf_{int(time.time())}.csv")
    ap.add_argument("--warm", action="store_true", help="Send a warm-up turn before measuring")
    args = ap.parse_args()

    rows: list[dict] = []

    if args.warm:
        print("warming up...", file=sys.stderr)
        await one_turn(args.url, "hi")

    for cat, prompts in CATEGORIES:
        print(f"\n=== {cat} ===", file=sys.stderr)
        for p in prompts:
            res = await one_turn(args.url, p)
            res["category"] = cat
            res["prompt"] = p[:60]
            rows.append(res)
            print(
                f"  {cat:15s} route={res['route_ms']!s:>6s}ms ttft={res['ttft_ms']!s:>6s}ms "
                f"total={res['total_ms']!s:>6s}ms backend={res['backend']:10s} src={res['source']}",
                file=sys.stderr,
            )

    out_path = Path(args.out)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "category",
                "prompt",
                "backend",
                "source",
                "route_ms",
                "ttft_ms",
                "total_ms",
            ],
        )
        w.writeheader()
        w.writerows(rows)

    print(f"\nwrote {len(rows)} rows -> {out_path}\n", file=sys.stderr)
    print(
        f"{'category':16s} {'n':>3s}  {'route p50':>9s} {'route p95':>9s}  {'ttft p50':>9s} {'ttft p95':>9s}  {'total p50':>9s}"
    )
    for cat, _ in CATEGORIES:
        cat_rows = [r for r in rows if r["category"] == cat]
        rmedian = pct([r["route_ms"] for r in cat_rows], 50)
        rp95 = pct([r["route_ms"] for r in cat_rows], 95)
        tmedian = pct([r["ttft_ms"] for r in cat_rows], 50)
        tp95 = pct([r["ttft_ms"] for r in cat_rows], 95)
        ttotalmedian = pct([r["total_ms"] for r in cat_rows], 50)
        print(
            f"{cat:16s} {len(cat_rows):3d}  {str(rmedian):>9s} {str(rp95):>9s}  "
            f"{str(tmedian):>9s} {str(tp95):>9s}  {str(ttotalmedian):>9s}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
