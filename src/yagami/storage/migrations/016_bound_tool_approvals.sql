ALTER TABLE tool_approvals ADD COLUMN subject_id TEXT;
ALTER TABLE tool_approvals ADD COLUMN schema_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_tool_approvals_subject
  ON tool_approvals(project_id, subject_id, created_at DESC);
