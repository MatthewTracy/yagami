"""Read-only status for connected MCP servers. See skills/mcp_manager.py for
the actual client/connection logic - this just reports what it found.
Connection failures are logged at startup and don't crash boot (one bad
server config shouldn't take down the app), so this endpoint is how you'd
actually notice one didn't come up.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..skills.mcp_manager import get_manager

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("")
async def mcp_status() -> dict:
    manager = get_manager()
    tools = manager.status() if manager is not None else []
    return {"connected": manager is not None, "tools": tools, "count": len(tools)}
