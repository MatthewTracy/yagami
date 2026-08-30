# Official Yagami integrations

These packages keep framework-specific dependencies out of the Yagami data
plane while preserving a single governed API:

| Package | Intended registry | Publication status | Purpose |
| --- | --- | --- | --- |
| `langchain-yagami` | PyPI | Pending first publication | LangChain chat model, governance preflight, and evidence metadata |
| `llama-index-llms-yagami` | PyPI | Pending first publication | LlamaIndex LLM integration |
| `llama-index-embeddings-yagami` | PyPI | Pending first publication | LlamaIndex embeddings through Yagami policy |
| `@yagami/ai-sdk-provider` | npm | Pending first publication | Vercel AI SDK provider |

Official adapters use lockstep versions with Yagami until 1.0. They communicate
with Yagami over its OpenAI-compatible HTTP API and never bypass policy,
evidence, trust-zone, or approval enforcement.

Until a package is marked **Published** here, use the generic OpenAI-compatible
recipes in the documentation or install that adapter from a checked-out source
tree. The release workflow is ready; registry namespace ownership and trusted
publisher setup are the remaining account-side prerequisites.

