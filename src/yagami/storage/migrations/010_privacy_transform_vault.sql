CREATE TABLE IF NOT EXISTS privacy_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  placeholder TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  nonce BLOB NOT NULL,
  ciphertext BLOB NOT NULL,
  value_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  UNIQUE(request_id, placeholder)
);
CREATE INDEX IF NOT EXISTS idx_privacy_tokens_expiry ON privacy_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_privacy_tokens_request ON privacy_tokens(request_id);
