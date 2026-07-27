from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .coordination import Coordinator, LocalCoordinator
from .telemetry.costs import spend_project_today_usd


class ProjectLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests_per_minute: int = Field(default=120, ge=1, le=1_000_000)
    max_concurrent_requests: int = Field(default=8, ge=1, le=10_000)
    daily_spend_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    allowed_purposes: list[str] = Field(default_factory=list)
    allowed_jurisdictions: list[str] = Field(default_factory=list)


class ProjectsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0.0"
    defaults: ProjectLimits = Field(default_factory=ProjectLimits)
    projects: dict[str, ProjectLimits] = Field(default_factory=dict)


class ProjectLimitError(RuntimeError):
    def __init__(self, message: str, *, code: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class ProjectRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime_ns: int | None = None
        self._document = ProjectsDocument()
        self.reload(force=True)

    @property
    def document(self) -> ProjectsDocument:
        self.reload()
        return self._document

    def reload(self, *, force: bool = False) -> bool:
        if not self.path.exists():
            return False
        stat = self.path.stat()
        if not force and stat.st_mtime_ns == self._mtime_ns:
            return False
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"projects file {self.path} must contain an object")
        self._document = ProjectsDocument.model_validate(raw)
        self._mtime_ns = stat.st_mtime_ns
        return True

    def limits_for(self, project_id: str) -> ProjectLimits:
        document = self.document
        return document.projects.get(project_id, document.defaults)


class ProjectGovernor:
    def __init__(
        self,
        registry: ProjectRegistry,
        coordinator: Coordinator | None = None,
        *,
        slot_ttl_seconds: int = 300,
    ) -> None:
        self.registry = registry
        self.coordinator = coordinator or LocalCoordinator()
        self.slot_ttl_seconds = slot_ttl_seconds

    async def check_request(
        self,
        *,
        project_id: str,
        purpose: str,
        jurisdiction: str | None,
    ) -> None:
        limits = self.registry.limits_for(project_id)
        if limits.allowed_purposes and purpose not in limits.allowed_purposes:
            raise ProjectLimitError(
                f"purpose {purpose!r} is not allowed for project {project_id!r}",
                code="purpose_not_allowed",
            )
        if limits.allowed_jurisdictions and (
            jurisdiction is None or jurisdiction not in limits.allowed_jurisdictions
        ):
            raise ProjectLimitError(
                f"jurisdiction {jurisdiction!r} is not allowed for project {project_id!r}",
                code="jurisdiction_not_allowed",
            )
        retry_after = await self.coordinator.rate_limit(
            project_id,
            limit=limits.requests_per_minute,
            window_seconds=60,
        )
        if retry_after is not None:
            raise ProjectLimitError(
                f"project {project_id!r} exceeded {limits.requests_per_minute} requests/minute",
                code="rate_limit_exceeded",
                retry_after=retry_after,
            )

    async def spend_blocked(self, project_id: str) -> bool:
        cap = self.registry.limits_for(project_id).daily_spend_usd
        if cap is None or cap <= 0:
            return False
        return await spend_project_today_usd(project_id) >= cap

    @asynccontextmanager
    async def slot(self, project_id: str) -> AsyncIterator[None]:
        limits = self.registry.limits_for(project_id)
        token = await self.coordinator.acquire_slot(
            project_id,
            limit=limits.max_concurrent_requests,
            ttl_seconds=self.slot_ttl_seconds,
        )
        if token is None:
            raise ProjectLimitError(
                f"project {project_id!r} has too many concurrent requests",
                code="concurrency_limit_exceeded",
                retry_after=1,
            )
        try:
            yield
        finally:
            await self.coordinator.release_slot(project_id, token)
