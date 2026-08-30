# Compare Yagami with gateways and guardrails

Yagami is an enforcement gateway for **where context may go and what an agent
may execute**. It combines deterministic routing restrictions, inherited
context labels, governed tools, and content-free decision evidence. It is not
automatically the best tool for every AI safety or gateway problem.

| If your primary need is... | Start with | Why |
| --- | --- | --- |
| Broad provider coverage, load balancing, virtual keys, and spend management | [LiteLLM](https://docs.litellm.ai/) | It supports a much wider provider surface and mature gateway routing/operations. |
| Application-level input/output validators or structured generation | [Guardrails AI](https://guardrailsai.com/guardrails/docs) | Its validator ecosystem and structured-data workflow are the center of the product. |
| Programmable dialog, retrieval, input, execution, and output rails | [NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/about-nemo-guardrails-library/rail-types) | It offers richer conversational-flow and model-based rail composition. |
| Deep PII recognition and de-identification across text, images, or structured data | [Presidio](https://microsoft.github.io/presidio/) | It is a specialized privacy detection/anonymization toolkit with extensible recognizers and operators. |
| Dedicated prompt-injection, agent-alignment, and insecure-code scanners | [LlamaFirewall](https://meta-llama.github.io/PurpleLlama/LlamaFirewall/docs/documentation/about-llamafirewall) | Its purpose-built scanners provide defense depth that Yagami's built-in rules do not claim to replace. |
| The smallest path to one provider with no extra data plane | A provider's SDK | Direct integration has fewer moving parts when centralized governance is unnecessary. |
| Deterministic local-only handling after a sensitive label, one-time tool approvals, and content-free evidence | **Yagami** | Those controls are enforced together at a shared OpenAI-compatible and MCP boundary. |

## The important distinction

Detection and enforcement are different jobs. A detector estimates whether
content is sensitive or malicious. An enforcement point decides whether that
content can reach a destination or capability. Yagami's differentiator is the
second job: after classification or a caller-provided label, policy can make a
non-probabilistic decision such as “PHI may use only device backends” or “this
tool requires a short-lived approval bound to this identity and schema.”

This does not make detection perfect. Strict callers should declare known data
labels, fail closed, and test organization-specific cases.

## Common combinations

- Put Yagami in front of LiteLLM when LiteLLM should own provider breadth,
  retries, and billing while Yagami owns context policy and evidence.
- Connect Presidio Analyzer to Yagami when broader PII recognition should raise
  the policy sensitivity before routing.
- Run Guardrails AI, NeMo Guardrails, or LlamaFirewall in the application or as
  a detector layer when their validators, dialog controls, or scanners are
  required; keep Yagami as the destination and execution boundary.
- Stay with a direct SDK for low-risk prototypes. Add a gateway only when the
  operational or governance benefit is worth another service boundary.

## Scope and maturity

Yagami is alpha software. It supports fewer providers and has less production
history than established gateways. Its published benchmark corpus is intended
to make specific configurations inspectable, not to claim universal security
or compliance. Review the [limitations](index.md#the-core-guarantee),
[threat model](threat-model.md), and [evaluation method](benchmarks.md) before
using it for a strict workload.
