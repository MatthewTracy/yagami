CREATE TABLE IF NOT EXISTS tool_approvals (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  tools TEXT NOT NULL,
  purpose TEXT,
  ticket TEXT,
  created_by TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER,
  consumed_request_id TEXT,
  revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tool_approvals_project
  ON tool_approvals(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_approvals_expiry
  ON tool_approvals(expires_at);
