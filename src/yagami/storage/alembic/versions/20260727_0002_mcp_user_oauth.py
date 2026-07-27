"""Add encrypted user-bound MCP OAuth credentials.

Revision ID: 20260727_0002
Revises: 20260727_0001
Create Date: 2026-07-27
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from alembic import op

from yagami.storage.schema import mcp_oauth_credentials, mcp_oauth_states

revision = "20260727_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    mcp_oauth_states.create(bind, checkfirst=True)
    mcp_oauth_credentials.create(bind, checkfirst=True)
    applied_at = int(time.time() * 1000)
    statement = (
        "INSERT INTO schema_migrations(version, applied_at)"
        " VALUES(:version, :applied_at) ON CONFLICT(version) DO NOTHING"
        if bind.dialect.name == "postgresql"
        else "INSERT OR IGNORE INTO schema_migrations(version, applied_at)"
        " VALUES(:version, :applied_at)"
    )
    op.execute(
        sa.text(statement).bindparams(
            version="020_mcp_user_oauth",
            applied_at=applied_at,
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "MCP OAuth credentials are not destructively downgraded; revoke them or restore a backup"
    )
