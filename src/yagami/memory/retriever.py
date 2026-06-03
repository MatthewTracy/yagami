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
import struct
from dataclasses import dataclass

from ..router.schema import Sensitivity
from ..storage.db import get_db
from .embedder import Embedder

log = logging.getLogger("yagami.memory.retriever")


@dataclass
class Hit:
    id: int
    role: str
    text: str
    sensitivity: Sensitivity
    session_id: str
    distance: float | None  # smaller = closer; None for FTS hits
    source: str  # "vec" or "fts"


def _vec_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _sens(value: str) -> Sensitivity:
    try:
        return Sensitivity(value)
    except (TypeError, ValueError):
        return Sensitivity.NONE


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
    ]


class Retriever:
    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    async def fetch(
        self,
        query: str,
        *,
        k: int = 5,
        exclude_session: str | None = None,
        current_sens: Sensitivity = Sensitivity.NONE,
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
        vec = await self._embedder.embed(query)
        if vec is not None:
            hits.extend(await self._vec_search(vec, k=k, exclude_session=exclude_session))
        if len(hits) < k:
            seen = {h.id for h in hits}
            remaining = k - len(hits)
            for h in await self._fts_search(query, k=remaining, exclude_session=exclude_session):
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
    ) -> list[Hit]:
        db = get_db()
        params: list = [_vec_blob(vec), k * 3]  # over-fetch so the post-filter has room
        where_excl = ""
        if exclude_session:
            where_excl = " AND o.session_id != ?"
            params.append(exclude_session)
        try:
            async with db.execute(
                f"""
                SELECT o.id, o.role, o.text, o.sensitivity, o.session_id, v.distance
                FROM observations_vec v
                JOIN observations o ON o.id = v.rowid
                WHERE v.embedding MATCH ? AND k = ? {where_excl}
                  AND o.embedding_status = 'ready'
                ORDER BY v.distance ASC
                LIMIT ?
                """,
                (*params, k),
            ) as cur:
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
            )
            for r in rows
        ]

    async def _fts_search(
        self,
        query: str,
        *,
        k: int,
        exclude_session: str | None,
    ) -> list[Hit]:
        db = get_db()
        # FTS5's MATCH wants the bare keyword string. Replace any double-quote
        # so it parses; strip non-alphanum so a single weird char doesn't error.
        cleaned = query.replace('"', "").strip()
        if not cleaned:
            return []
        params: list = [cleaned]
        where_excl = ""
        if exclude_session:
            where_excl = " AND o.session_id != ?"
            params.append(exclude_session)
        try:
            async with db.execute(
                f"""
                SELECT o.id, o.role, o.text, o.sensitivity, o.session_id
                FROM observations_fts f
                JOIN observations o ON o.id = f.rowid
                WHERE f.text MATCH ? {where_excl}
                ORDER BY rank
                LIMIT ?
                """,
                (*params, k),
            ) as cur:
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
            )
            for r in rows
        ]
