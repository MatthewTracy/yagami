-- Persist user-supplied vision inputs with their message so reloading a
-- conversation restores the exact model context. Store decoded bytes instead
-- of base64 to avoid the base64 size overhead in SQLite.
CREATE TABLE IF NOT EXISTS message_attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    media_type TEXT    NOT NULL CHECK (media_type IN (
        'image/png', 'image/jpeg', 'image/gif', 'image/webp'
    )),
    data       BLOB    NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS message_attachments_message_idx
    ON message_attachments(message_id, id);
