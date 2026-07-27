CREATE TABLE mcp_oauth_states (
  state_hash TEXT PRIMARY KEY,
  server_name TEXT NOT NULL,
  project_id TEXT NOT NULL,
  subject_hash TEXT NOT NULL,
  nonce BLOB NOT NULL,
  ciphertext BLOB NOT NULL,
  wrapped_key BLOB NOT NULL,
  wrapping_key_id TEXT NOT NULL,
  key_epoch INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER
);

CREATE INDEX idx_mcp_oauth_states_expiry
  ON mcp_oauth_states(expires_at);

CREATE TABLE mcp_oauth_credentials (
  server_name TEXT NOT NULL,
  project_id TEXT NOT NULL,
  subject_hash TEXT NOT NULL,
  nonce BLOB NOT NULL,
  ciphertext BLOB NOT NULL,
  wrapped_key BLOB NOT NULL,
  wrapping_key_id TEXT NOT NULL,
  key_epoch INTEGER NOT NULL,
  access_expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  last_used_at INTEGER NOT NULL,
  PRIMARY KEY(server_name, project_id, subject_hash)
);

CREATE INDEX idx_mcp_oauth_credentials_expiry
  ON mcp_oauth_credentials(access_expires_at);
