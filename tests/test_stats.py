from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from yagami.main import build_app
from yagami.storage.db import get_db


@pytest.mark.asyncio
async def test_stats_endpoint_empty():
    app = build_app()
    async with app.router.lifespan_context(app):
        # Wipe decisions so the test is deterministic regardless of prior runs.
        await get_db().execute("DELETE FROM decisions")
        await get_db().commit()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/stats")
            assert r.status_code == 200
            data = r.json()
            assert data["total_turns"] == 0
            assert data["total_cost_usd"] == 0.0
            assert data["by_backend"] == []


@pytest.mark.asyncio
async def test_stats_aggregates_by_backend():
    app = build_app()
    async with app.router.lifespan_context(app):
        db = get_db()
        await db.execute("DELETE FROM decisions")
        await db.execute("DELETE FROM sessions WHERE id = 's1'")
        now = int(time.time() * 1000)
        await db.execute(
            "INSERT INTO sessions(id, created_at, updated_at) VALUES ('s1', ?, ?)",
            (now, now),
        )
        # Two anthropic turns @ $0.01, three ollama turns @ $0.
        for cost in (0.01, 0.02):
            await db.execute(
                """INSERT INTO decisions(session_id, backend, is_local, reason, source,
                       classification, scrubbed_preview, created_at, cost_usd,
                       tokens_in, tokens_out, t_first_token_ms, t_total_ms)
                   VALUES (?, 'anthropic', 0, 'r', 'classifier',
                       '{"source":"classifier"}', '', ?, ?, 10, 20, 300, 1200)""",
                ("s1", now, cost),
            )
        for _ in range(3):
            await db.execute(
                """INSERT INTO decisions(session_id, backend, is_local, reason, source,
                       classification, scrubbed_preview, created_at, cost_usd,
                       tokens_in, tokens_out, t_first_token_ms, t_total_ms)
                   VALUES (?, 'ollama', 1, 'r', 'rules-fast-path',
                       '{"source":"rules-fast-path"}', '', ?, 0, 5, 10, 100, 500)""",
                ("s1", now),
            )
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/stats?days=30")
            assert r.status_code == 200
            data = r.json()
            assert data["total_turns"] == 5
            assert data["total_cost_usd"] == 0.03
            assert len(data["by_backend"]) == 2
            by_name = {b["backend"]: b for b in data["by_backend"]}
            assert by_name["anthropic"]["turns"] == 2
            assert by_name["anthropic"]["cost_usd"] == 0.03
            assert by_name["ollama"]["turns"] == 3
            assert by_name["ollama"]["cost_usd"] == 0.0
            sources = {s["source"]: s["turns"] for s in data["by_classification_source"]}
            assert sources["classifier"] == 2
            assert sources["rules-fast-path"] == 3


@pytest.mark.asyncio
async def test_feedback_persists_and_overwrites():
    app = build_app()
    async with app.router.lifespan_context(app):
        db = get_db()
        await db.execute("DELETE FROM decisions")
        await db.execute("DELETE FROM feedback")
        await db.execute("DELETE FROM sessions WHERE id = 's1'")
        now = int(time.time() * 1000)
        await db.execute(
            "INSERT INTO sessions(id, created_at, updated_at) VALUES ('s1', ?, ?)",
            (now, now),
        )
        cur = await db.execute(
            """INSERT INTO decisions(session_id, backend, is_local, reason, source,
                   classification, scrubbed_preview, created_at)
               VALUES ('s1','ollama',1,'r','test','{}','',?)""",
            (now,),
        )
        decision_id = cur.lastrowid
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(f"/api/decisions/{decision_id}/feedback", json={"rating": 1})
            assert r.status_code == 200
            assert r.json()["rating"] == 1

            # Flip to -1; should overwrite, not duplicate.
            r = await c.post(f"/api/decisions/{decision_id}/feedback", json={"rating": -1})
            assert r.status_code == 200

        async with db.execute(
            "SELECT rating, COUNT(*) FROM feedback WHERE decision_id = ? GROUP BY rating",
            (decision_id,),
        ) as cur2:
            rows = [tuple(r) for r in await cur2.fetchall()]
        assert rows == [(-1, 1)]


@pytest.mark.asyncio
async def test_feedback_validates_rating():
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/decisions/999999/feedback", json={"rating": 5})
            assert r.status_code == 400


@pytest.mark.asyncio
async def test_feedback_404_for_unknown_decision():
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/decisions/99999999/feedback", json={"rating": 1})
            assert r.status_code == 404
