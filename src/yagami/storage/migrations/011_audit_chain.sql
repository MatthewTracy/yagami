CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  project_id TEXT NOT NULL,
  request_id TEXT,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL,
  key_id TEXT NOT NULL,
  UNIQUE(project_id, event_hash)
);
CREATE INDEX IF NOT EXISTS idx_audit_events_project
  ON audit_events(project_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_events_request
  ON audit_events(request_id, id);
