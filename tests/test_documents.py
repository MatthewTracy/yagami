from __future__ import annotations

from pathlib import Path

import pytest

from yagami.memory import documents
from yagami.memory.chunker import chunk
from yagami.storage.db import close_db, get_db, open_db


class _FakeEmbedder:
    """Deterministic per-text vector, same convention as test_retriever.py's
    fake - same text -> same vector -> distance 0."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.model = "fake"
        self._fail_on = fail_on or set()

    async def embed(self, text: str):
        if text in self._fail_on:
            return None
        seed = abs(hash(text)) % 1000
        return [float(seed) / 1000.0] + [0.0] * 383


@pytest.fixture
async def docdb(tmp_path: Path):
    await open_db(tmp_path / "docs.db")
    yield get_db()
    await close_db()


# ---- chunker: unbounded document chunking ----


def test_chunk_default_still_caps_at_eight():
    long_text = ("Sentence number %d in a very long chat turn. " % 1) * 2000
    out = chunk(long_text)
    assert len(out) <= 8


def test_chunk_max_chunks_override_allows_more():
    long_text = "Paragraph one is here.\n\n" * 3000
    out = chunk(long_text, max_chunks=500)
    assert len(out) > 8


# ---- index_folder / search / list_sources / delete_source ----


@pytest.mark.asyncio
async def test_index_folder_reads_txt_and_md(docdb, tmp_path):
    (tmp_path / "notes.txt").write_text("The quarterly report shows steady growth.")
    (tmp_path / "readme.md").write_text("# Setup\n\nRun `pip install -e .` to get started.")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")  # unsupported, should be skipped

    summary = await documents.index_folder(tmp_path, embedder=_FakeEmbedder())
    assert summary["files_indexed"] == 2
    assert summary["files_skipped"] == 0  # unsupported suffix isn't even attempted
    assert summary["chunks_written"] >= 2

    sources = await documents.list_sources()
    assert len(sources) == 2
    paths = {s["source_path"] for s in sources}
    assert str(tmp_path / "notes.txt") in paths
    assert str(tmp_path / "readme.md") in paths


@pytest.mark.asyncio
async def test_reindexing_replaces_not_duplicates(docdb, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("version one of the document")
    await documents.index_folder(tmp_path, embedder=_FakeEmbedder())

    f.write_text("version two, completely different content")
    summary = await documents.index_folder(tmp_path, embedder=_FakeEmbedder())
    assert summary["files_indexed"] == 1

    hits = await documents.search("version two", embedder=_FakeEmbedder(), k=5)
    assert any("version two" in h["text"] for h in hits)
    assert not any("version one" in h["text"] for h in hits)


@pytest.mark.asyncio
async def test_search_finds_indexed_content(docdb, tmp_path):
    (tmp_path / "doc.txt").write_text("The mango tree in the garden is blooming this spring.")
    emb = _FakeEmbedder()
    await documents.index_folder(tmp_path, embedder=emb)

    hits = await documents.search("mango tree", embedder=emb, k=5)
    assert len(hits) > 0
    assert any("mango" in h["text"] for h in hits)


@pytest.mark.asyncio
async def test_search_empty_query_returns_nothing(docdb, tmp_path):
    (tmp_path / "doc.txt").write_text("some content")
    await documents.index_folder(tmp_path, embedder=_FakeEmbedder())
    assert await documents.search("", embedder=_FakeEmbedder()) == []


@pytest.mark.asyncio
async def test_failed_embedding_falls_back_to_fts(docdb, tmp_path):
    text = "I love writing haiku about mango trees"
    (tmp_path / "doc.txt").write_text(text)
    failing = _FakeEmbedder(fail_on={text})
    await documents.index_folder(tmp_path, embedder=failing)

    # Embedding failed -> row is 'failed', not 'ready', so vec search won't
    # return it - but FTS5 still indexes the raw text via the trigger, so a
    # keyword search should still find it.
    hits = await documents.search("haiku mango", embedder=failing, k=5)
    assert any("haiku" in h["text"] for h in hits)


@pytest.mark.asyncio
async def test_delete_source_removes_all_its_chunks(docdb, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("content to be deleted later")
    await documents.index_folder(tmp_path, embedder=_FakeEmbedder())

    source_path = str(f)
    deleted = await documents.delete_source(source_path)
    assert deleted >= 1
    assert await documents.list_sources() == []


@pytest.mark.asyncio
async def test_delete_nonexistent_source_returns_zero(docdb):
    assert await documents.delete_source("/no/such/file.txt") == 0


@pytest.mark.asyncio
async def test_unsupported_file_types_are_skipped(docdb, tmp_path):
    (tmp_path / "binary.exe").write_bytes(b"\x00\x01\x02")
    summary = await documents.index_folder(tmp_path, embedder=_FakeEmbedder())
    assert summary["files_indexed"] == 0
