"""introduce users and auth_sessions

Revision ID: 0007_users_sessions
Revises: 0006_agencies
Create Date: 2026-11-03 00:00:00

Phase 13.2 — authentication.

Pure DDL: creates two new tables. Does not modify agencies, clients,
applications, or any other existing table. No seed data is inserted;
users are created via the operator CLI (app.cli create-user).

The downgrade drops both new tables and therefore destroys all users
and sessions. That is a destructive operation against a deployed
environment; document it in release notes if Phase 13.2 is rolled back.

Email uniqueness is enforced case-insensitively via a functional
unique index on lower(email). The application normalizes input to
lowercase before insert, but the DB-level index is the source of truth
and defends against bugs in that normalization.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_users_sessions"
down_revision: Union[str, None] = "0006_agencies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "agency_id",
            sa.BigInteger,
            sa.ForeignKey("agencies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_agency_id", "users", ["agency_id"])
    op.execute(
        "CREATE UNIQUE INDEX ux_users_email_lower ON users (lower(email))"
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index(
        "ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.execute("DROP INDEX IF EXISTS ux_users_email_lower")
    op.drop_index("ix_users_agency_id", table_name="users")
    op.drop_table("users")
