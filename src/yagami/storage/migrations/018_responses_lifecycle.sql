CREATE TABLE IF NOT EXISTS response_jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  decision_id INTEGER,
  model TEXT NOT NULL,
  status TEXT NOT NULL,
  previous_response_id TEXT,
  conversation_id TEXT,
  input_json TEXT NOT NULL,
  output_json TEXT,
  metadata_json TEXT NOT NULL,
  error_json TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_response_jobs_project
  ON response_jobs(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_response_jobs_conversation
  ON response_jobs(project_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_response_jobs_expiry
  ON response_jobs(expires_at);

CREATE TABLE IF NOT EXISTS response_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  response_id TEXT NOT NULL,
  sequence_number INTEGER NOT NULL,
  event_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(response_id, sequence_number),
  FOREIGN KEY(response_id) REFERENCES response_jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_response_events_resume
  ON response_events(response_id, sequence_number);
