-- v0.3: folder-based document knowledge base (memory/documents.py).
--
-- Deliberately a separate table from `observations`, not a shared one:
-- documents have no session_id, no sensitivity/TTL (they're reference
-- material the user explicitly indexed, not chat turns the classifier
-- tagged), and no per-message chunk cap. Re-indexing a source_path replaces
-- its rows rather than appending duplicates.

CREATE TABLE IF NOT EXISTS kb_documents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path      TEXT    NOT NULL,
    chunk_index      INTEGER NOT NULL DEFAULT 0,
    text             TEXT    NOT NULL,
    created_at       INTEGER NOT NULL,
    embedding_status TEXT    NOT NULL DEFAULT 'pending'  -- 'pending' / 'ready' / 'failed'
);

CREATE INDEX IF NOT EXISTS kb_documents_source_idx ON kb_documents(source_path);
CREATE INDEX IF NOT EXISTS kb_documents_embedding_status_idx ON kb_documents(embedding_status) WHERE embedding_status = 'pending';

CREATE VIRTUAL TABLE IF NOT EXISTS kb_documents_fts USING fts5(
    text,
    content='kb_documents',
    content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS kb_documents_fts_insert AFTER INSERT ON kb_documents BEGIN
    INSERT INTO kb_documents_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS kb_documents_fts_delete AFTER DELETE ON kb_documents BEGIN
    INSERT INTO kb_documents_fts(kb_documents_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS kb_documents_fts_update AFTER UPDATE OF text ON kb_documents BEGIN
    INSERT INTO kb_documents_fts(kb_documents_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO kb_documents_fts(rowid, text) VALUES (new.id, new.text);
END;

-- 384 dim matches all-minilm, same as observations_vec.
CREATE VIRTUAL TABLE IF NOT EXISTS kb_documents_vec USING vec0(
    embedding float[384]
);
