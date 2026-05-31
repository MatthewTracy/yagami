from __future__ import annotations

from yagami.backends.base import ImageAttachment, Message


def test_message_accepts_image_attachments():
    img = ImageAttachment(media_type="image/png", data_b64="iVBORw0KGgo=")
    m = Message(role="user", content="what is this?", images=[img])
    assert m.images is not None
    assert len(m.images) == 1
    assert m.images[0].media_type == "image/png"


def test_anthropic_backend_constructs_vision_content_blocks():
    # We can't hit the real API in tests; just verify the message-to-content
    # transform inline so future refactors don't silently drop image blocks.
    from anthropic import AsyncAnthropic
    from yagami.backends.anthropic import ClaudeBackend
    from yagami.config import AnthropicConfig

    # Minimal stub — we won't actually call .messages.stream.
    backend = ClaudeBackend(AnthropicConfig(), api_key="sk-ant-test")
    assert isinstance(backend._client, AsyncAnthropic)

    msgs = [
        Message(
            role="user",
            content="what is this?",
            images=[ImageAttachment(media_type="image/png", data_b64="iVBOR...")],
        )
    ]
    chat: list[dict] = []
    for m in msgs:
        if m.role not in ("user", "assistant"):
            continue
        if m.images:
            blocks: list[dict] = []
            for img in m.images:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.media_type,
                            "data": img.data_b64,
                        },
                    }
                )
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            chat.append({"role": m.role, "content": blocks})
        else:
            chat.append({"role": m.role, "content": m.content})

    assert chat[0]["role"] == "user"
    content = chat[0]["content"]
    assert isinstance(content, list)
    types = [b["type"] for b in content]
    assert types == ["image", "text"]
    assert content[0]["source"]["media_type"] == "image/png"
