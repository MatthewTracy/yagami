"""Prove that a governed tool can run while PHI stays on the device.

Requires a normal Yagami server with Ollama and a tool-capable local model.
The synthetic prompt contains no real patient information.
"""

from __future__ import annotations

import os

import httpx


def main() -> None:
    base_url = os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
    key = os.getenv("YAGAMI_API_KEY", "")
    headers = {"authorization": f"Bearer {key}"} if key else {}
    payload = {
        "model": "yagami-auto",
        "messages": [
            {
                "role": "user",
                "content": "Calculate 37 * 19 for this private patient record.",
            }
        ],
        "metadata": {
            "sensitivity": "phi_medical",
            "purpose": "synthetic-clinical-calculation",
        },
    }

    response = httpx.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    body = response.json()
    policy = body["yagami"]["policy"]
    if policy["candidate_trust_zone"] not in {"device", "private_network"}:
        raise RuntimeError("sensitive request left the configured private trust boundary")
    if policy["effective_sensitivity"] != "phi_medical":
        raise RuntimeError("caller-declared PHI label was not preserved")
    executions = policy.get("tool_executions", [])
    if not executions or not executions[0]["ok"]:
        raise RuntimeError("local governed calculator did not execute successfully")

    print("PASS sensitive route:", body["model"], policy["candidate_trust_zone"])
    print("PASS governed tool evidence:", executions[0])
    print("Answer:", body["choices"][0]["message"]["content"])


if __name__ == "__main__":
    main()
