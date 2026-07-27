"""Retrieve top-K relevant observations for the current turn.

Default path: vector search via sqlite-vec over `observations_vec`.
Fallback path: FTS5 over `observations_fts` for rows whose embeddings
aren't ready yet (or failed).

PHI quarantine - defense in depth (the source of truth is policy.py, but
the retriever ALSO refuses to surface PHI/secret observations when the
current turn isn't itself PHI). This stops a clinical aside from leaking
into an unrelated cloud-text turn via injection.
"""

from __future__ import annotations

import logging
import json
import math
import struct
from dataclasses import dataclass

from ..router.schema import DataLabel, Sensitivity
from ..storage.db import get_db
from .embedder import EmbedderProtocol

log = logging.getLogger("yagami.memory.retriever")

_VEC_QUERY = """
    SELECT o.id, o.role, o.text, o.sensitivity, o.session_id, v.distance,
           o.project_id, o.data_labels, o.provenance
    FROM observations_vec v
    JOIN observations o ON o.id = v.rowid
    WHERE v.embedding MATCH ? AND k = ?
      AND o.project_id = ? AND o.quarantined = 0
      AND o.embedding_status = 'ready'
    ORDER BY v.distance ASC
    LIMIT ?
"""
_VEC_QUERY_EXCLUDING_SESSION = """
    SELECT o.id, o.role, o.text, o.sensitivity, o.session_id, v.distance,
           o.project_id, o.data_labels, o.provenance
    FROM observations_vec v
    JOIN observations o ON o.id = v.rowid
    WHERE v.embedding MATCH ? AND k = ? AND o.project_id = ?
      AND o.session_id != ? AND o.quarantined = 0
      AND o.embedding_status = 'ready'
    ORDER BY v.distance ASC
    LIMIT ?
"""
_FTS_QUERY = """
    SELECT o.id, o.role, o.text, o.sensitivity, o.session_id,
           o.project_id, o.data_labels, o.provenance
    FROM observations_fts f
    JOIN observations o ON o.id = f.rowid
    WHERE f.text MATCH ? AND o.project_id = ? AND o.quarantined = 0
    ORDER BY rank
    LIMIT ?
"""
_FTS_QUERY_EXCLUDING_SESSION = """
    SELECT o.id, o.role, o.text, o.sensitivity, o.session_id,
           o.project_id, o.data_labels, o.provenance
    FROM observations_fts f
    JOIN observations o ON o.id = f.rowid
    WHERE f.text MATCH ? AND o.project_id = ? AND o.session_id != ?
      AND o.quarantined = 0
    ORDER BY rank
    LIMIT ?
"""
_POSTGRES_VEC_QUERY = """
    SELECT o.id, o.role, o.text, o.sensitivity, o.session_id, v.embedding,
           o.project_id, o.data_labels, o.provenance
    FROM observations_vec v
    JOIN observations o ON o.id = v.rowid
    WHERE o.project_id = ? AND o.quarantined = 0
      AND o.embedding_status = 'ready'
    ORDER BY o.id DESC
    LIMIT 1000
"""
_POSTGRES_VEC_QUERY_EXCLUDING_SESSION = _POSTGRES_VEC_QUERY.replace(
    "    ORDER BY o.id DESC",
    "      AND o.session_id != ?\n    ORDER BY o.id DESC",
)
_POSTGRES_FTS_QUERY = """
    SELECT o.id, o.role, o.text, o.sensitivity, o.session_id,
           o.project_id, o.data_labels, o.provenance
    FROM observations o
    WHERE o.project_id = ? AND o.quarantined = 0
      AND to_tsvector('simple', o.text) @@ plainto_tsquery('simple', ?)
    ORDER BY ts_rank_cd(
      to_tsvector('simple', o.text), plainto_tsquery('simple', ?)
    ) DESC
    LIMIT ?
"""
_POSTGRES_FTS_QUERY_EXCLUDING_SESSION = _POSTGRES_FTS_QUERY.replace(
    "      AND to_tsvector",
    "      AND o.session_id != ?\n      AND to_tsvector",
)


@dataclass
class Hit:
    id: int
    role: str
    text: str
    sensitivity: Sensitivity
    session_id: str
    distance: float | None  # smaller = closer; None for FTS hits
    source: str  # "vec" or "fts"
    project_id: str = "local"
    data_labels: tuple[DataLabel, ...] = ()
    provenance: str = "chat"


def _vec_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _cosine_distance(left: list[float], blob: bytes) -> float:
    if not blob or len(blob) % 4:
        return 1.0
    right = struct.unpack(f"<{len(blob) // 4}f", blob)
    if len(left) != len(right):
        return 1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 1.0
    return max(0.0, 1.0 - dot / (left_norm * right_norm))


def _sens(value: str) -> Sensitivity:
    try:
        return Sensitivity(value)
    except (TypeError, ValueError):
        return Sensitivity.NONE


def _labels(value: str) -> tuple[DataLabel, ...]:
    try:
        raw = json.loads(value)
    except (TypeError, ValueError):
        return ()
    if not isinstance(raw, list):
        return ()
    labels: list[DataLabel] = []
    for item in raw:
        try:
            labels.append(DataLabel(item))
        except (TypeError, ValueError):
            continue
    return tuple(labels)


def _phi_safe_filter(hits: list[Hit], current_sens: Sensitivity) -> list[Hit]:
    """Drop PHI/secret hits when the current turn ISN'T PHI.

    If the current turn is itself PHI, surfacing prior PHI is fine (the
    chat is staying local). If it isn't, we never want PHI to ride along
    into a cloud-text call as injected context - the policy enforces
    cloud-block on history-PHI anyway, but retrieval shouldn't even
    return the rows.
    """
    if current_sens in (Sensitivity.PHI, Sensitivity.PHI_MEDICAL):
        return hits
    return [
        h
        for h in hits
        if h.sensitivity not in (Sensitivity.PHI, Sensitivity.PHI_MEDICAL, Sensitivity.SECRET)
        and DataLabel.PHI not in h.data_labels
        and DataLabel.SECRET not in h.data_labels
    ]


class Retriever:
    def __init__(self, embedder: EmbedderProtocol | None) -> None:
        self._embedder = embedder

    async def fetch(
        self,
        query: str,
        *,
        k: int = 5,
        exclude_session: str | None = None,
        current_sens: Sensitivity = Sensitivity.NONE,
        project_id: str = "local",
    ) -> list[Hit]:
        """Return up to `k` observations ranked by vector distance, with
        an FTS5 backfill if the vec table has fewer than k hits.

        `exclude_session` skips rows from the named session - we don't
        want retrieval to inject *this same session's* messages back at
        the model, since they're already in the chat history.
        """
        query = query.strip()
        if not query:
            return []
        hits: list[Hit] = []
        vec = await self._embedder.embed(query) if self._embedder is not None else None
        if vec is not None:
            hits.extend(
                await self._vec_search(
                    vec, k=k, exclude_session=exclude_session, project_id=project_id
                )
            )
        if len(hits) < k:
            seen = {h.id for h in hits}
            remaining = k - len(hits)
            for h in await self._fts_search(
                query,
                k=remaining,
                exclude_session=exclude_session,
                project_id=project_id,
            ):
                if h.id not in seen:
                    hits.append(h)
                    seen.add(h.id)
        return _phi_safe_filter(hits, current_sens)[:k]

    async def _vec_search(
        self,
        vec: list[float],
        *,
        k: int,
        exclude_session: str | None,
        project_id: str,
    ) -> list[Hit]:
        db = get_db()
        if db.dialect == "postgresql":
            query = (
                _POSTGRES_VEC_QUERY_EXCLUDING_SESSION if exclude_session else _POSTGRES_VEC_QUERY
            )
            parameters = (project_id, exclude_session) if exclude_session else (project_id,)
            async with db.execute(
                query,
                parameters,
            ) as cursor:
                candidates = await cursor.fetchall()
            ranked = sorted(
                ((_cosine_distance(vec, bytes(row[5])), row) for row in candidates),
                key=lambda item: item[0],
            )[:k]
            return [
                Hit(
                    id=int(row[0]),
                    role=str(row[1]),
                    text=str(row[2]),
                    sensitivity=_sens(row[3]),
                    session_id=str(row[4]),
                    distance=distance,
                    source="vec",
                    project_id=str(row[6]),
                    data_labels=_labels(row[7]),
                    provenance=str(row[8]),
                )
                for distance, row in ranked
            ]
        params: list = [
            _vec_blob(vec),
            k * 3,
            project_id,
        ]  # over-fetch so the post-filter has room
        if exclude_session:
            params.append(exclude_session)
        query = _VEC_QUERY_EXCLUDING_SESSION if exclude_session else _VEC_QUERY
        try:
            async with db.execute(query, (*params, k)) as cur:
                rows = await cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - vec query failure shouldn't break retrieval
            log.warning("vec search failed: %s; falling back to FTS only", exc)
            return []
        return [
            Hit(
                id=int(r[0]),
                role=str(r[1]),
                text=str(r[2]),
                sensitivity=_sens(r[3]),
                session_id=str(r[4]),
                distance=float(r[5]),
                source="vec",
                project_id=str(r[6]),
                data_labels=_labels(r[7]),
                provenance=str(r[8]),
            )
            for r in rows
        ]

    async def _fts_search(
        self,
        query: str,
        *,
        k: int,
        exclude_session: str | None,
        project_id: str,
    ) -> list[Hit]:
        db = get_db()
        # FTS5's MATCH wants the bare keyword string. Replace any double-quote
        # so it parses; strip non-alphanum so a single weird char doesn't error.
        cleaned = query.replace('"', "").strip()
        if not cleaned:
            return []
        if db.dialect == "postgresql":
            sql = _POSTGRES_FTS_QUERY_EXCLUDING_SESSION if exclude_session else _POSTGRES_FTS_QUERY
            parameters = (
                (project_id, exclude_session, cleaned, cleaned, k)
                if exclude_session
                else (project_id, cleaned, cleaned, k)
            )
            async with db.execute(
                sql,
                parameters,
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                Hit(
                    id=int(row[0]),
                    role=str(row[1]),
                    text=str(row[2]),
                    sensitivity=_sens(row[3]),
                    session_id=str(row[4]),
                    distance=None,
                    source="fts",
                    project_id=str(row[5]),
                    data_labels=_labels(row[6]),
                    provenance=str(row[7]),
                )
                for row in rows
            ]
        params: list = [cleaned, project_id]
        if exclude_session:
            params.append(exclude_session)
        sql = _FTS_QUERY_EXCLUDING_SESSION if exclude_session else _FTS_QUERY
        try:
            async with db.execute(sql, (*params, k)) as cur:
                rows = await cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - FTS MATCH can throw on weird tokens
            log.warning("fts search failed: %s", exc)
            return []
        return [
            Hit(
                id=int(r[0]),
                role=str(r[1]),
                text=str(r[2]),
                sensitivity=_sens(r[3]),
                session_id=str(r[4]),
                distance=None,
                source="fts",
                project_id=str(r[5]),
                data_labels=_labels(r[6]),
                provenance=str(r[7]),
            )
            for r in rows
        ]
