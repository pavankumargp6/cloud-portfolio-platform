"""notification logs schema

Revision ID: 0004_notifications
Revises: 0003_incidents
Create Date: 2026-11-02 00:00:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_notifications"
down_revision: Union[str, None] = "0003_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE notification_logs (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL
                REFERENCES incidents(id) ON DELETE CASCADE,
            notification_type VARCHAR(50) NOT NULL,
            recipient VARCHAR(255) NOT NULL,
            status VARCHAR(20) NOT NULL,
            error_message TEXT,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_notification_logs_incident "
        "ON notification_logs (incident_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notification_logs_incident")
    op.execute("DROP TABLE IF EXISTS notification_logs")
