# Official Yagami integrations

These packages keep framework-specific dependencies out of the Yagami data
plane while preserving a single governed API:

| Package | Registry | Purpose |
| --- | --- | --- |
| `langchain-yagami` | PyPI | LangChain chat model, governance preflight, and evidence metadata |
| `llama-index-llms-yagami` | PyPI | LlamaIndex LLM integration |
| `llama-index-embeddings-yagami` | PyPI | LlamaIndex embeddings through Yagami policy |
| `@yagami/ai-sdk-provider` | npm | Vercel AI SDK provider |

Official adapters use lockstep versions with Yagami until 1.0. They communicate
with Yagami over its OpenAI-compatible HTTP API and never bypass policy,
evidence, trust-zone, or approval enforcement.

