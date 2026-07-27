# langchain-yagami

Route LangChain model calls through the self-hosted Yagami context firewall.
Policy decisions, approvals, backend identity, and content-free evidence remain
available in LangChain response metadata.

```python
from langchain_yagami import ChatYagami

model = ChatYagami(
    base_url="http://127.0.0.1:8000/v1",
    api_key="replace-with-your-project-key",
)
answer = model.invoke("Summarize this document without exposing secrets.")
print(answer.content)
print(answer.response_metadata["yagami"])
```

`ChatYagami` supports synchronous, asynchronous, streaming, and tool-bound
LangChain calls. Use `YagamiGovernanceClient` for an explicit policy preflight
or to issue a short-lived, identity-bound tool approval.

