"""backup records and verifications

Revision ID: 0005_backups
Revises: 0004_notifications
Create Date: 2026-11-03 00:00:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005_backups"
down_revision: Union[str, None] = "0004_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE backup_records (
            id BIGSERIAL PRIMARY KEY,
            application_id BIGINT NOT NULL
                REFERENCES applications(id) ON DELETE CASCADE,
            backup_type VARCHAR(50) NOT NULL,
            location VARCHAR(1024) NOT NULL,
            size_bytes BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_backup_records_app "
        "ON backup_records (application_id, created_at DESC)"
    )

    op.execute("""
        CREATE TABLE backup_verifications (
            id BIGSERIAL PRIMARY KEY,
            backup_id BIGINT NOT NULL
                REFERENCES backup_records(id) ON DELETE CASCADE,
            status VARCHAR(20) NOT NULL,
            duration_ms BIGINT,
            error_message TEXT,
            verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_backup_verifications_backup "
        "ON backup_verifications (backup_id, verified_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_backup_verifications_backup")
    op.execute("DROP TABLE IF EXISTS backup_verifications")
    op.execute("DROP INDEX IF EXISTS ix_backup_records_app")
    op.execute("DROP TABLE IF EXISTS backup_records")
