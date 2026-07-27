-- Pattern-based redaction cannot guarantee that a preview is free of
-- personal, medical, secret, or proprietary content. Yagami's decision
-- evidence is content-free, so purge historical excerpts. The column stays
-- until the planned 1.0 table rebuild because it was originally NOT NULL.
UPDATE decisions SET scrubbed_preview = '';
