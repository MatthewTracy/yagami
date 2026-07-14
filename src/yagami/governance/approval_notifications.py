"""Content-free outbound notifications for tool approval lifecycle events."""

from __future__ import annotations

from typing import Any, Literal

import httpx


class ApprovalNotifier:
    def __init__(
        self,
        url: str,
        *,
        format: Literal["json", "slack", "teams"] = "json",
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not url.casefold().startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("approval webhook URL must use HTTPS unless it is loopback")
        self.url = url
        self.format = format
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def payload(self, event: str, details: dict[str, Any]) -> dict[str, Any]:
        safe = {
            "event": event,
            "approval_id": details.get("approval_id"),
            "project_id": details.get("project_id"),
            "tools": details.get("tools", []),
            "purpose": details.get("purpose"),
            "expires_at": details.get("expires_at"),
        }
        summary = (
            f"Yagami {event}: {safe['approval_id']} for project {safe['project_id']} "
            f"({', '.join(str(tool) for tool in safe['tools'])})"
        )
        if self.format == "slack":
            return {"text": summary, "metadata": {"event_type": event, "event_payload": safe}}
        if self.format == "teams":
            return {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "type": "AdaptiveCard",
                            "version": "1.4",
                            "body": [{"type": "TextBlock", "text": summary, "wrap": True}],
                        },
                    }
                ],
            }
        return safe

    async def notify(self, event: str, details: dict[str, Any]) -> None:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport, follow_redirects=False
        ) as client:
            response = await client.post(self.url, json=self.payload(event, details))
            response.raise_for_status()
