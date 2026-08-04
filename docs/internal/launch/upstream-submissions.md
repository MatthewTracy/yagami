# Upstream submission drafts

Submit only packages that are already public and installable. Follow each
upstream project's current contribution template rather than pasting these
paragraphs unchanged.

## LangChain integration directory

`langchain-yagami` provides a chat-model integration and governance middleware
for a self-hosted Yagami gateway. It exposes routing/evidence metadata without
including prompt content and passes the LangChain standard integration tests.

- Package: [PYPI_LANGCHAIN_URL]
- Source: [SOURCE_URL]
- Docs/example: [LANGCHAIN_DOC_URL]
- Test evidence: [CI_URL]

## LlamaIndex integrations hub

`llama-index-llms-yagami` routes LlamaIndex model requests through Yagami's
policy boundary. `llama-index-embeddings-yagami` is submitted separately only
after the embedding API and package are public and release-tested.

- LLM package: [PYPI_LLAMAINDEX_LLM_URL]
- Embedding package, if released: [PYPI_LLAMAINDEX_EMBED_URL]
- Docs/example: [LLAMAINDEX_DOC_URL]

## Vercel AI SDK community providers

`@yagami/ai-sdk-provider` connects the AI SDK to a self-hosted Yagami endpoint
and carries npm provenance. The submission must link to the public npm package,
typed example, supported feature matrix, and current compatibility test.

## Awesome-list entry

Yagami — MIT-licensed, self-hosted AI context and execution firewall for
governed model, retrieval, memory, MCP, and tool access. ([source]) ([docs])

Target only relevant lists whose contribution rules permit self-submissions.
Use one factual sentence; do not submit the same generic copy everywhere.

