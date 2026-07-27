ALTER TABLE observations ADD COLUMN project_id TEXT NOT NULL DEFAULT 'local';
ALTER TABLE observations ADD COLUMN data_labels TEXT NOT NULL DEFAULT '[]';
ALTER TABLE observations ADD COLUMN provenance TEXT NOT NULL DEFAULT 'chat';
ALTER TABLE observations ADD COLUMN policy_hash TEXT;
ALTER TABLE observations ADD COLUMN quarantined INTEGER NOT NULL DEFAULT 0;
ALTER TABLE observations ADD COLUMN quarantine_reason TEXT;

CREATE INDEX IF NOT EXISTS observations_project_idx
  ON observations(project_id, id);
CREATE INDEX IF NOT EXISTS observations_quarantine_idx
  ON observations(project_id, quarantined, id)
  WHERE quarantined = 1;
