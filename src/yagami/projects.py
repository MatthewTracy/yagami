from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import yaml
from pydantic import BaseModel, ConfigDict, Field

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
    def __init__(self, registry: ProjectRegistry) -> None:
        self.registry = registry
        self._rate_lock = asyncio.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._concurrency_lock = asyncio.Lock()
        self._active: dict[str, int] = defaultdict(int)

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
        now = time.monotonic()
        async with self._rate_lock:
            window = self._requests[project_id]
            while window and window[0] <= now - 60:
                window.popleft()
            if len(window) >= limits.requests_per_minute:
                retry_after = max(1, int(60 - (now - window[0])))
                raise ProjectLimitError(
                    f"project {project_id!r} exceeded {limits.requests_per_minute} requests/minute",
                    code="rate_limit_exceeded",
                    retry_after=retry_after,
                )
            window.append(now)

    async def spend_blocked(self, project_id: str) -> bool:
        cap = self.registry.limits_for(project_id).daily_spend_usd
        if cap is None or cap <= 0:
            return False
        return await spend_project_today_usd(project_id) >= cap

    @asynccontextmanager
    async def slot(self, project_id: str) -> AsyncIterator[None]:
        limits = self.registry.limits_for(project_id)
        async with self._concurrency_lock:
            if self._active[project_id] >= limits.max_concurrent_requests:
                raise ProjectLimitError(
                    f"project {project_id!r} has too many concurrent requests",
                    code="concurrency_limit_exceeded",
                    retry_after=1,
                )
            self._active[project_id] += 1
        try:
            yield
        finally:
            async with self._concurrency_lock:
                self._active[project_id] = max(0, self._active[project_id] - 1)
