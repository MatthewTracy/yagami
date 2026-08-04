# Show HN draft

## Title

Show HN: Yagami — a self-hosted context firewall for AI agents

## Post

I built Yagami because model gateways answered “which model should receive
this request?” but not the harder question: “is this context allowed to leave
the device, enter retrieval, persist in memory, or reach this tool?”

Yagami is an MIT-licensed, self-hosted gateway that classifies context, applies
deterministic policy, governs model/retrieval/memory/tool access, and records
content-free evidence. A sensitive session can remain on a local Ollama model
and still use approved local tools; arguments and results are not copied into
the policy passport.

Try it: `uvx yagami demo`

Repository: [REPOSITORY_URL]

Five-minute tour: [DEMO_URL]

Versioned benchmark and limitations: [REPORT_URL]

I would especially value feedback from people operating local models, MCP
servers, RAG systems, or regulated-data workloads. The main design trade-off
is intentionally conservative: if required policy or verification cannot run,
the sensitive path fails closed.

## Prepared answers

**Why not LiteLLM?** LiteLLM is strong for broad provider access, budgets, and
gateway operations. Yagami focuses on data labels, trust zones, governed
retrieval/memory/tools, and content-free evidence. They can be composed. See
[COMPARISON_URL].

**Why not Guardrails AI or NeMo Guardrails?** Those projects are useful for
input/output validation and conversational rails. Yagami's boundary is the
execution path: where labeled context may travel and which capabilities may
act on it.

**Is this HIPAA compliant?** Yagami is a technical control, not a compliance
certification. Deployment, contracts, access controls, logging, and operating
procedures still determine compliance.

**Does content enter telemetry or audit logs?** Telemetry is disabled by
default. Policy evidence is designed to contain labels, hashes, timing, and
reason codes—not prompts, tool arguments/results, retrieved text, or stable
customer identifiers. See [PRIVACY_TESTS_URL].

