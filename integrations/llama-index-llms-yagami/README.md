# llama-index-llms-yagami

Use Yagami as a LlamaIndex chat and function-calling model while keeping model,
retrieval, memory, and tool traffic behind Yagami policy.

```python
from llama_index.llms.yagami import YagamiLLM

llm = YagamiLLM(api_key="replace-with-your-project-key")
response = llm.complete("Summarize the retrieved context safely.")
print(response)
```

The default endpoint is `http://127.0.0.1:8000/v1`. Set `YAGAMI_BASE_URL` and
`YAGAMI_API_KEY`, or pass them to the constructor.

