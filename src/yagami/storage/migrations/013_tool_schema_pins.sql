CREATE TABLE IF NOT EXISTS tool_schema_pins (
  project_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  pinned_hash TEXT NOT NULL,
  pending_hash TEXT,
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  approved_at INTEGER,
  approved_by TEXT,
  PRIMARY KEY(project_id, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_tool_schema_pins_project
  ON tool_schema_pins(project_id, tool_name);
