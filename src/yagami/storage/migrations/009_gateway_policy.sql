ALTER TABLE sessions ADD COLUMN channel TEXT NOT NULL DEFAULT 'chat';
ALTER TABLE sessions ADD COLUMN project_id TEXT;
CREATE INDEX IF NOT EXISTS idx_sessions_channel ON sessions(channel, updated_at DESC);

ALTER TABLE decisions ADD COLUMN request_id TEXT;
ALTER TABLE decisions ADD COLUMN project_id TEXT;
ALTER TABLE decisions ADD COLUMN channel TEXT NOT NULL DEFAULT 'chat';
ALTER TABLE decisions ADD COLUMN policy_decision TEXT;
ALTER TABLE decisions ADD COLUMN request_context TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_request_id
  ON decisions(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_decisions_project
  ON decisions(project_id, created_at DESC);
