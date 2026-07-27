"""Create the portable Yagami production schema.

Revision ID: 20260727_0001
Revises:
Create Date: 2026-07-27
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from alembic import op

from yagami.storage.schema import metadata

revision = "20260727_0001"
down_revision = None
branch_labels = None
depends_on = None

_SQLITE_FTS = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS observations_vec USING vec0(
      embedding float[384]
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS kb_documents_vec USING vec0(
      embedding float[384]
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
      text, content='observations', content_rowid='id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS observations_fts_insert
    AFTER INSERT ON observations BEGIN
      INSERT INTO observations_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS observations_fts_delete
    AFTER DELETE ON observations BEGIN
      INSERT INTO observations_fts(observations_fts, rowid, text)
      VALUES('delete', old.id, old.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS observations_fts_update
    AFTER UPDATE OF text ON observations BEGIN
      INSERT INTO observations_fts(observations_fts, rowid, text)
      VALUES('delete', old.id, old.text);
      INSERT INTO observations_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS kb_documents_fts USING fts5(
      text, content='kb_documents', content_rowid='id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS kb_documents_fts_insert
    AFTER INSERT ON kb_documents BEGIN
      INSERT INTO kb_documents_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS kb_documents_fts_delete
    AFTER DELETE ON kb_documents BEGIN
      INSERT INTO kb_documents_fts(kb_documents_fts, rowid, text)
      VALUES('delete', old.id, old.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS kb_documents_fts_update
    AFTER UPDATE OF text ON kb_documents BEGIN
      INSERT INTO kb_documents_fts(kb_documents_fts, rowid, text)
      VALUES('delete', old.id, old.text);
      INSERT INTO kb_documents_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    tables = [
        table
        for table in metadata.sorted_tables
        if dialect != "sqlite" or not table.info.get("sqlite_virtual")
    ]
    metadata.create_all(bind, tables=tables, checkfirst=True)
    if dialect == "sqlite":
        for statement in _SQLITE_FTS:
            op.execute(sa.text(statement))
    applied_at = int(time.time() * 1000)
    for version in (
        "001_init",
        "002_decision_timings",
        "003_costs",
        "004_feedback",
        "005_observations",
        "006_decision_profile",
        "007_kb_documents",
        "008_message_attachments",
        "009_gateway_policy",
        "010_privacy_transform_vault",
        "011_audit_chain",
        "012_tool_approvals",
        "013_tool_schema_pins",
        "014_content_free_decisions",
        "015_memory_governance",
        "016_bound_tool_approvals",
        "017_audit_outbox",
        "018_responses_lifecycle",
        "019_transform_envelope_keys",
    ):
        if dialect == "postgresql":
            op.execute(
                sa.text(
                    "INSERT INTO schema_migrations(version, applied_at)"
                    " VALUES(:version, :applied_at) ON CONFLICT(version) DO NOTHING"
                ).bindparams(version=version, applied_at=applied_at)
            )
        else:
            op.execute(
                sa.text(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at)"
                    " VALUES(:version, :applied_at)"
                ).bindparams(version=version, applied_at=applied_at)
            )


def downgrade() -> None:
    raise RuntimeError(
        "The baseline schema is not destructively downgraded; restore a verified backup instead"
    )
