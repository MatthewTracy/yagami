from llama_index.llms.yagami import YagamiLLM


def test_defaults_target_governed_api() -> None:
    llm = YagamiLLM(
        base_url="http://yagami.test/v1",
        api_key="test-key",
        metadata={"purpose": "retrieval"},
    )
    assert llm.api_base == "http://yagami.test/v1"
    assert llm.model == "yagami-auto"
    assert llm.is_chat_model is True
    assert llm.is_function_calling_model is True
    assert llm.additional_kwargs["metadata"] == {"purpose": "retrieval"}
