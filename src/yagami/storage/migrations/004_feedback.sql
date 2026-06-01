CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    rating      INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS feedback_decision_idx ON feedback(decision_id);
CREATE INDEX IF NOT EXISTS feedback_created_idx ON feedback(created_at DESC);
