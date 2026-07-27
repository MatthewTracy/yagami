"""Portable relational schema used by Alembic for clean installations."""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()
id_type = sa.BigInteger().with_variant(sa.Integer, "sqlite")

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("updated_at", sa.BigInteger, nullable=False),
    sa.Column("title", sa.Text),
    sa.Column("channel", sa.Text, nullable=False, server_default="chat"),
    sa.Column("project_id", sa.Text),
)
sa.Index("idx_sessions_channel", sessions.c.channel, sessions.c.updated_at.desc())

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column(
        "session_id",
        sa.Text,
        sa.ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
)
sa.Index("idx_messages_session", messages.c.session_id, messages.c.id)

decisions = sa.Table(
    "decisions",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column(
        "session_id",
        sa.Text,
        sa.ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("backend", sa.Text, nullable=False),
    sa.Column("is_local", sa.Integer, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("classification", sa.Text, nullable=False),
    sa.Column("scrubbed_preview", sa.Text, nullable=False, server_default=""),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("t_classify_ms", sa.Integer),
    sa.Column("t_first_token_ms", sa.Integer),
    sa.Column("t_total_ms", sa.Integer),
    sa.Column("tokens_in", sa.Integer),
    sa.Column("tokens_out", sa.Integer),
    sa.Column("cost_usd", sa.Float),
    sa.Column("profile", sa.Text),
    sa.Column("request_id", sa.Text),
    sa.Column("project_id", sa.Text),
    sa.Column("channel", sa.Text, nullable=False, server_default="chat"),
    sa.Column("policy_decision", sa.Text),
    sa.Column("request_context", sa.Text),
)
sa.Index("idx_decisions_session", decisions.c.session_id, decisions.c.id)
sa.Index(
    "idx_decisions_request_id",
    decisions.c.request_id,
    unique=True,
    postgresql_where=decisions.c.request_id.is_not(None),
    sqlite_where=decisions.c.request_id.is_not(None),
)
sa.Index("idx_decisions_project", decisions.c.project_id, decisions.c.created_at.desc())

feedback = sa.Table(
    "feedback",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column(
        "decision_id",
        id_type,
        sa.ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("rating", sa.Integer, sa.CheckConstraint("rating IN (-1, 1)"), nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
)
sa.Index("feedback_decision_idx", feedback.c.decision_id)
sa.Index("feedback_created_idx", feedback.c.created_at.desc())

observations = sa.Table(
    "observations",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column("session_id", sa.Text, nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("sensitivity", sa.Text, nullable=False, server_default="none"),
    sa.Column("source_app", sa.Text, nullable=False, server_default="chat"),
    sa.Column("ttl_until", sa.BigInteger),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("chunk_index", sa.Integer, nullable=False, server_default="0"),
    sa.Column("parent_id", id_type),
    sa.Column("embedding_status", sa.Text, nullable=False, server_default="pending"),
    sa.Column("project_id", sa.Text, nullable=False, server_default="local"),
    sa.Column("data_labels", sa.Text, nullable=False, server_default="[]"),
    sa.Column("provenance", sa.Text, nullable=False, server_default="chat"),
    sa.Column("policy_hash", sa.Text),
    sa.Column("quarantined", sa.Integer, nullable=False, server_default="0"),
    sa.Column("quarantine_reason", sa.Text),
)
sa.Index("observations_session_idx", observations.c.session_id, observations.c.id)
sa.Index(
    "observations_ttl_idx",
    observations.c.ttl_until,
    postgresql_where=observations.c.ttl_until.is_not(None),
    sqlite_where=observations.c.ttl_until.is_not(None),
)
sa.Index(
    "observations_embedding_status_idx",
    observations.c.embedding_status,
    postgresql_where=observations.c.embedding_status == "pending",
    sqlite_where=observations.c.embedding_status == "pending",
)
sa.Index(
    "observations_parent_idx",
    observations.c.parent_id,
    postgresql_where=observations.c.parent_id.is_not(None),
    sqlite_where=observations.c.parent_id.is_not(None),
)
sa.Index("observations_project_idx", observations.c.project_id, observations.c.id)
sa.Index(
    "observations_quarantine_idx",
    observations.c.project_id,
    observations.c.quarantined,
    observations.c.id,
    postgresql_where=observations.c.quarantined == 1,
    sqlite_where=observations.c.quarantined == 1,
)

observations_vec = sa.Table(
    "observations_vec",
    metadata,
    sa.Column("rowid", id_type, primary_key=True),
    sa.Column("embedding", sa.LargeBinary, nullable=False),
    info={"sqlite_virtual": True},
)

kb_documents = sa.Table(
    "kb_documents",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column("source_path", sa.Text, nullable=False),
    sa.Column("chunk_index", sa.Integer, nullable=False, server_default="0"),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("embedding_status", sa.Text, nullable=False, server_default="pending"),
)
sa.Index("kb_documents_source_idx", kb_documents.c.source_path)
sa.Index(
    "kb_documents_embedding_status_idx",
    kb_documents.c.embedding_status,
    postgresql_where=kb_documents.c.embedding_status == "pending",
    sqlite_where=kb_documents.c.embedding_status == "pending",
)

kb_documents_vec = sa.Table(
    "kb_documents_vec",
    metadata,
    sa.Column("rowid", id_type, primary_key=True),
    sa.Column("embedding", sa.LargeBinary, nullable=False),
    info={"sqlite_virtual": True},
)

message_attachments = sa.Table(
    "message_attachments",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column(
        "message_id",
        id_type,
        sa.ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "media_type",
        sa.Text,
        sa.CheckConstraint("media_type IN ('image/png','image/jpeg','image/gif','image/webp')"),
        nullable=False,
    ),
    sa.Column("data", sa.LargeBinary, nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
)
sa.Index(
    "message_attachments_message_idx",
    message_attachments.c.message_id,
    message_attachments.c.id,
)

privacy_tokens = sa.Table(
    "privacy_tokens",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column("request_id", sa.Text, nullable=False),
    sa.Column("project_id", sa.Text, nullable=False),
    sa.Column("placeholder", sa.Text, nullable=False),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("nonce", sa.LargeBinary, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("value_hash", sa.Text, nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("expires_at", sa.BigInteger, nullable=False),
    sa.Column("wrapped_key", sa.LargeBinary),
    sa.Column("wrapping_key_id", sa.Text),
    sa.Column("key_epoch", sa.Integer),
    sa.UniqueConstraint("request_id", "placeholder"),
)
sa.Index("idx_privacy_tokens_expiry", privacy_tokens.c.expires_at)
sa.Index("idx_privacy_tokens_request", privacy_tokens.c.request_id)
sa.Index(
    "idx_privacy_tokens_wrapping_key",
    privacy_tokens.c.wrapping_key_id,
    privacy_tokens.c.key_epoch,
)

audit_events = sa.Table(
    "audit_events",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("project_id", sa.Text, nullable=False),
    sa.Column("request_id", sa.Text),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("previous_hash", sa.Text, nullable=False),
    sa.Column("event_hash", sa.Text, nullable=False),
    sa.Column("key_id", sa.Text, nullable=False),
    sa.UniqueConstraint("project_id", "event_hash"),
)
sa.Index("idx_audit_events_project", audit_events.c.project_id, audit_events.c.id)
sa.Index("idx_audit_events_request", audit_events.c.request_id, audit_events.c.id)

tool_approvals = sa.Table(
    "tool_approvals",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("project_id", sa.Text, nullable=False),
    sa.Column("token_hash", sa.Text, nullable=False, unique=True),
    sa.Column("tools", sa.Text, nullable=False),
    sa.Column("purpose", sa.Text),
    sa.Column("ticket", sa.Text),
    sa.Column("created_by", sa.Text),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("expires_at", sa.BigInteger, nullable=False),
    sa.Column("consumed_at", sa.BigInteger),
    sa.Column("consumed_request_id", sa.Text),
    sa.Column("revoked_at", sa.BigInteger),
    sa.Column("subject_id", sa.Text),
    sa.Column("schema_hash", sa.Text),
)
sa.Index(
    "idx_tool_approvals_project", tool_approvals.c.project_id, tool_approvals.c.created_at.desc()
)
sa.Index("idx_tool_approvals_expiry", tool_approvals.c.expires_at)
sa.Index(
    "idx_tool_approvals_subject",
    tool_approvals.c.project_id,
    tool_approvals.c.subject_id,
    tool_approvals.c.created_at.desc(),
)

tool_schema_pins = sa.Table(
    "tool_schema_pins",
    metadata,
    sa.Column("project_id", sa.Text, primary_key=True),
    sa.Column("tool_name", sa.Text, primary_key=True),
    sa.Column("pinned_hash", sa.Text, nullable=False),
    sa.Column("pending_hash", sa.Text),
    sa.Column("first_seen_at", sa.BigInteger, nullable=False),
    sa.Column("last_seen_at", sa.BigInteger, nullable=False),
    sa.Column("approved_at", sa.BigInteger),
    sa.Column("approved_by", sa.Text),
)
sa.Index(
    "idx_tool_schema_pins_project", tool_schema_pins.c.project_id, tool_schema_pins.c.tool_name
)

audit_key_epochs = sa.Table(
    "audit_key_epochs",
    metadata,
    sa.Column("key_id", sa.Text, primary_key=True),
    sa.Column("first_used_at", sa.BigInteger, nullable=False),
    sa.Column("last_used_at", sa.BigInteger, nullable=False),
)

audit_outbox = sa.Table(
    "audit_outbox",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column(
        "audit_event_id",
        id_type,
        sa.ForeignKey("audit_events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    sa.Column("project_id", sa.Text, nullable=False),
    sa.Column("event_json", sa.Text, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("next_attempt_at", sa.BigInteger, nullable=False),
    sa.Column("delivered_at", sa.BigInteger),
    sa.Column("dead_lettered_at", sa.BigInteger),
    sa.Column("last_error_code", sa.Text),
    sa.Column("created_at", sa.BigInteger, nullable=False),
)
sa.Index(
    "idx_audit_outbox_pending",
    audit_outbox.c.delivered_at,
    audit_outbox.c.dead_lettered_at,
    audit_outbox.c.next_attempt_at,
    audit_outbox.c.id,
)
sa.Index("idx_audit_outbox_project", audit_outbox.c.project_id, audit_outbox.c.id)

response_jobs = sa.Table(
    "response_jobs",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("project_id", sa.Text, nullable=False),
    sa.Column("request_id", sa.Text, nullable=False),
    sa.Column("decision_id", id_type),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("previous_response_id", sa.Text),
    sa.Column("conversation_id", sa.Text),
    sa.Column("input_json", sa.Text, nullable=False),
    sa.Column("output_json", sa.Text),
    sa.Column("metadata_json", sa.Text, nullable=False),
    sa.Column("error_json", sa.Text),
    sa.Column("cancel_requested", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("updated_at", sa.BigInteger, nullable=False),
    sa.Column("expires_at", sa.BigInteger),
)
sa.Index("idx_response_jobs_project", response_jobs.c.project_id, response_jobs.c.created_at.desc())
sa.Index(
    "idx_response_jobs_conversation",
    response_jobs.c.project_id,
    response_jobs.c.conversation_id,
    response_jobs.c.created_at,
)
sa.Index("idx_response_jobs_expiry", response_jobs.c.expires_at)

response_events = sa.Table(
    "response_events",
    metadata,
    sa.Column("id", id_type, primary_key=True, autoincrement=True),
    sa.Column(
        "response_id",
        sa.Text,
        sa.ForeignKey("response_jobs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("sequence_number", sa.Integer, nullable=False),
    sa.Column("event_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.UniqueConstraint("response_id", "sequence_number"),
)
sa.Index(
    "idx_response_events_resume", response_events.c.response_id, response_events.c.sequence_number
)

schema_migrations = sa.Table(
    "schema_migrations",
    metadata,
    sa.Column("version", sa.Text, primary_key=True),
    sa.Column("applied_at", sa.BigInteger, nullable=False),
)
