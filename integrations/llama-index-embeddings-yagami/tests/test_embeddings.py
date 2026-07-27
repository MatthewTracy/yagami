from llama_index.embeddings.yagami import YagamiEmbedding


def test_defaults_target_governed_embedding_api() -> None:
    embedding = YagamiEmbedding(
        base_url="http://yagami.test/v1",
        api_key="test-key",
    )
    assert embedding.api_base == "http://yagami.test/v1"
    assert embedding.model_name == "yagami-embedding"
    assert embedding.dimensions == 384
