from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/tool-schemas", tags=["tool-schemas"])


def _registry(request: Request):
    return request.app.state.runtime.tool_schemas


@router.get("")
async def list_pins(
    request: Request,
    project_id: str = Query(min_length=1, max_length=64),
) -> dict:
    rows = await _registry(request).list(project_id=project_id)
    return {"tool_schemas": rows, "count": len(rows)}


class ApproveBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    project_id: str = Field(min_length=1, max_length=64)


@router.post("/{tool_name}/approve")
async def approve(tool_name: str, body: ApproveBody, request: Request) -> dict:
    principal = getattr(request.state, "principal", None)
    approved = await _registry(request).approve_pending(
        project_id=body.project_id,
        tool_name=tool_name,
        approved_by=getattr(principal, "subject_id", None),
    )
    if not approved:
        raise HTTPException(404, "no pending schema drift for this tool")
    return {"ok": True, "project_id": body.project_id, "tool_name": tool_name}
