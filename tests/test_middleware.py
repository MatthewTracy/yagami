from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from yagami.middleware import RequestSizeLimitMiddleware


async def _echo_size(scope, receive, send) -> None:
    size = 0
    while True:
        message = await receive()
        size += len(message.get("body", b""))
        if not message.get("more_body"):
            break
    body = str(size).encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode("ascii"))],
        }
    )
    await send({"type": "http.response.body", "body": body})


@pytest.mark.asyncio
async def test_request_limit_rejects_content_length_before_endpoint() -> None:
    app = RequestSizeLimitMiddleware(_echo_size, max_bytes=16)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/", content=b"x" * 17)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_request_limit_counts_chunked_body() -> None:
    async def chunks():
        yield b"x" * 10
        yield b"y" * 10

    app = RequestSizeLimitMiddleware(_echo_size, max_bytes=16)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/", content=chunks())
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_request_limit_allows_bounded_body() -> None:
    app = RequestSizeLimitMiddleware(_echo_size, max_bytes=16)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/", content=b"safe")
    assert response.status_code == 200
    assert response.text == "4"
