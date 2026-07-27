from __future__ import annotations

import httpx
import pytest
import respx
from langchain_core.messages import HumanMessage
from langchain_yagami import ChatYagami


@respx.mock
def test_invoke_exposes_content_free_evidence() -> None:
    route = respx.post("http://yagami.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={
                "x-yagami-request-id": "ygm_test",
                "x-yagami-decision-id": "42",
                "x-yagami-backend": "ollama",
                "x-yagami-policy-hash": "abc123",
            },
            json={
                "id": "chatcmpl-test",
                "model": "ollama",
                "choices": [
                    {"message": {"role": "assistant", "content": "safe"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )
    )
    message = ChatYagami(base_url="http://yagami.test/v1", api_key="test").invoke(
        [HumanMessage("private input")]
    )
    assert message.content == "safe"
    assert message.response_metadata["yagami"] == {
        "request_id": "ygm_test",
        "decision_id": "42",
        "backend": "ollama",
        "policy_hash": "abc123",
    }
    assert "private input" not in str(message.response_metadata)
    assert route.calls[0].request.headers["authorization"] == "Bearer test"


@pytest.mark.asyncio
@respx.mock
async def test_async_invoke() -> None:
    respx.post("http://yagami.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "ollama",
                "choices": [
                    {"message": {"role": "assistant", "content": "safe"}, "finish_reason": "stop"}
                ],
                "usage": {},
            },
        )
    )
    message = await ChatYagami(base_url="http://yagami.test/v1").ainvoke("hello")
    assert message.content == "safe"
