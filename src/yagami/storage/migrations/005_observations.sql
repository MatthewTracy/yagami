-- v0.2.15: cross-session memory write path.
--
-- `observations` is the main row store; one row per chat message we deemed
-- worth remembering (assistant responses + non-trivial user messages, never
-- secrets). Embeddings live in the sqlite-vec virtual table `observations_vec`
-- and are joined on rowid == observations.id. FTS5 mirror gives us a
-- text-search fallback while embedding_status='pending' or 'failed'.
--
-- TTL conventions (set by stream.py write gate):
--   - PHI / PHI_MEDICAL: 7 days
--   - everything else:   90 days
--   - SECRET:            never written
-- A scheduled vacuum (v0.2.16) deletes rows where ttl_until < now.

CREATE TABLE IF NOT EXISTS observations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT    NOT NULL,
    role             TEXT    NOT NULL,                           -- 'user' / 'assistant'
    text             TEXT    NOT NULL,
    sensitivity      TEXT    NOT NULL DEFAULT 'none',            -- enum value of Sensitivity
    source_app       TEXT    NOT NULL DEFAULT 'chat',            -- 'chat', future: 'clipboard', 'screenshot'
    ttl_until        INTEGER,                                    -- millis; NULL = no expiry (rare)
    created_at       INTEGER NOT NULL,
    chunk_index      INTEGER NOT NULL DEFAULT 0,                 -- 0 if not chunked; else 0..N-1
    parent_id        INTEGER,                                    -- for chunks: id of the un-chunked parent observation
    embedding_status TEXT    NOT NULL DEFAULT 'pending'          -- 'pending' / 'ready' / 'failed' / 'skipped'
);

CREATE INDEX IF NOT EXISTS observations_session_idx          ON observations(session_id, id);
CREATE INDEX IF NOT EXISTS observations_ttl_idx              ON observations(ttl_until)        WHERE ttl_until IS NOT NULL;
CREATE INDEX IF NOT EXISTS observations_embedding_status_idx ON observations(embedding_status) WHERE embedding_status = 'pending';
CREATE INDEX IF NOT EXISTS observations_parent_idx           ON observations(parent_id)        WHERE parent_id IS NOT NULL;

-- FTS5 mirror for keyword fallback retrieval.
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    text,
    content='observations',
    content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS observations_fts_update AFTER UPDATE OF text ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO observations_fts(rowid, text) VALUES (new.id, new.text);
END;

-- sqlite-vec vector store. 384 dim matches all-minilm (v0.2.15 embed model).
-- If the extension fails to load, this CREATE will error and the whole
-- migration aborts — surfacing the problem at startup instead of silent
-- write-path failures later.
CREATE VIRTUAL TABLE IF NOT EXISTS observations_vec USING vec0(
    embedding float[384]
);
