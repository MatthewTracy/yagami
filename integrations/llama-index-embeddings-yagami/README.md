# llama-index-embeddings-yagami

Send LlamaIndex embedding requests through Yagami's governed `/v1/embeddings`
endpoint. Sensitive inputs are classified before the configured embedding
destination receives them, and policy can require a device or private-network
trust zone.

```python
from llama_index.embeddings.yagami import YagamiEmbedding

embed_model = YagamiEmbedding(api_key="replace-with-your-project-key")
vector = embed_model.get_text_embedding("private retrieval content")
```

