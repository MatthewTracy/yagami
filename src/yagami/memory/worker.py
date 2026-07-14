"""Background embedding worker.

Runs as a long-lived asyncio task spawned in main.py's lifespan. Polls the
observations table for `embedding_status='pending'` rows, embeds in small
batches via the Embedder, and writes back via store.write_embeddings.

Why polling instead of a real queue: the same SQLite DB is the queue.
Restart-safe (pending survives reboot), no second process, no Redis. The
trade-off is poll latency - currently every 2s; the write gate also nudges
the worker after each turn so fresh observations land within ~50ms.
"""

from __future__ import annotations

import asyncio
import logging

from . import store
from .embedder import Embedder

log = logging.getLogger("yagami.memory.worker")

POLL_INTERVAL_S = 2.0
BATCH_SIZE = 16
# Run the TTL vacuum every Nth poll iteration. With POLL_INTERVAL_S=2,
# 10800 iterations ≈ 6 hours. Cheap (one DELETE), but no reason to do
# it constantly.
VACUUM_EVERY_N_TICKS = 10800


class EmbeddingWorker:
    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="memory.worker")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                log.debug("memory worker cancelled")
            except Exception:  # noqa: BLE001 - stop must complete during application shutdown
                log.warning("memory worker failed while stopping", exc_info=True)
            self._task = None

    def nudge(self) -> None:
        """Hint to the loop that new pending rows exist - wakes it before
        the next poll. Safe to call from any coroutine."""
        self._wake.set()

    async def _loop(self) -> None:
        log.info("memory worker started (model=%s)", self._embedder.model)
        tick = 0
        while not self._stopping:
            try:
                processed = await self._drain_once()
                tick += 1
                if tick % VACUUM_EVERY_N_TICKS == 0:
                    try:
                        n = await store.delete_expired()
                        if n:
                            log.info("vacuum: deleted %d expired observations", n)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("vacuum failed: %s", exc)
                if processed == 0:
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=POLL_INTERVAL_S)
                    except asyncio.TimeoutError:
                        pass
                    self._wake.clear()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - never let one bad row kill the loop
                log.warning("worker loop iteration raised %s; sleeping", exc)
                await asyncio.sleep(POLL_INTERVAL_S)

    async def _drain_once(self) -> int:
        """Embed one batch. Returns the number of rows processed."""
        pending = await store.list_pending(limit=BATCH_SIZE)
        if not pending:
            return 0
        results: list[tuple[int, list[float] | None]] = []
        # Sequential embed; nomic-embed runs on the same Ollama daemon as the
        # generator, and Ollama already serializes requests per model. Going
        # parallel would just queue inside the daemon.
        for obs_id, text in pending:
            vec = await self._embedder.embed(text)
            results.append((obs_id, vec))
        await store.write_embeddings(results)
        log.debug("embedded %d observations", len(results))
        return len(results)
