# Community post drafts

## r/LocalLLaMA

**Title:** Governed local tool calling for sensitive AI sessions with Ollama

Yagami is an MIT-licensed context firewall that can pin PHI, secrets, and other
labeled context to a local trust zone while preserving governed tool use. The
gateway checks capability and policy before execution and emits content-free
evidence rather than copying arguments or results into its ledger.

The quickstart is `uvx yagami demo`. The release-tested Ollama model, exact
tool scenario, and limitations are here: [LOCAL_TOOL_DOC_URL]. I would value
reports from other tool-capable Ollama models and hardware, especially failure
cases.

## r/selfhosted and r/homelab

**Title:** Yagami: a self-hosted policy boundary for models, RAG, memory, and tools

Yagami sits between AI clients and model/tool infrastructure. It labels
context, maps destinations to trust zones, applies deterministic policy, and
keeps audit evidence content-free. It supports a local SQLite/Ollama path and
a production PostgreSQL/Redis/Kubernetes path.

Start locally with `uvx yagami demo` or the loopback-only Compose demo:
`docker compose -f compose.demo.yaml up`.

Repository: [REPOSITORY_URL]

Threat model and known limitations: [THREAT_MODEL_URL]

## Short release announcement

Yagami [VERSION] is out: [ONE_SENTENCE_RELEASE_OUTCOME].

- [VERIFIED_CHANGE_1]
- [VERIFIED_CHANGE_2]
- [VERIFIED_CHANGE_3]

Install: `pip install yagami==[VERSION]`

Signed artifacts, benchmark methodology, migration notes, and limitations:
[RELEASE_URL]

