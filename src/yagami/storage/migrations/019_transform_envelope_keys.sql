ALTER TABLE privacy_tokens ADD COLUMN wrapped_key BLOB;
ALTER TABLE privacy_tokens ADD COLUMN wrapping_key_id TEXT;
ALTER TABLE privacy_tokens ADD COLUMN key_epoch INTEGER;

CREATE INDEX IF NOT EXISTS idx_privacy_tokens_wrapping_key
  ON privacy_tokens(wrapping_key_id, key_epoch);
