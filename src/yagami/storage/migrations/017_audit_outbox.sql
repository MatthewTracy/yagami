CREATE TABLE IF NOT EXISTS audit_key_epochs (
  key_id TEXT PRIMARY KEY,
  first_used_at INTEGER NOT NULL,
  last_used_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  audit_event_id INTEGER NOT NULL UNIQUE,
  project_id TEXT NOT NULL,
  event_json TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL,
  delivered_at INTEGER,
  dead_lettered_at INTEGER,
  last_error_code TEXT,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(audit_event_id) REFERENCES audit_events(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_audit_outbox_pending
  ON audit_outbox(delivered_at, dead_lettered_at, next_attempt_at, id);
CREATE INDEX IF NOT EXISTS idx_audit_outbox_project
  ON audit_outbox(project_id, id);
