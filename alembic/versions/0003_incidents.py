"""incident management schema

Revision ID: 0003_incidents
Revises: 0002_monitoring
Create Date: 2026-11-01 00:00:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003_incidents"
down_revision: Union[str, None] = "0002_monitoring"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE incidents (
            id BIGSERIAL PRIMARY KEY,
            application_id BIGINT NOT NULL
                REFERENCES applications(id) ON DELETE CASCADE,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            acknowledged_at TIMESTAMPTZ,
            resolved_at TIMESTAMPTZ,
            acknowledged_by VARCHAR(255),
            resolved_by VARCHAR(255),
            resolution_notes TEXT
        )
    """)
    op.execute(
        "CREATE INDEX ix_incidents_app_status "
        "ON incidents (application_id, status)"
    )

    op.execute("""
        CREATE TABLE incident_events (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL
                REFERENCES incidents(id) ON DELETE CASCADE,
            event_type VARCHAR(50) NOT NULL,
            details TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_incident_events_incident_id "
        "ON incident_events (incident_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_incident_events_incident_id")
    op.execute("DROP TABLE IF EXISTS incident_events")
    op.execute("DROP INDEX IF EXISTS ix_incidents_app_status")
    op.execute("DROP TABLE IF EXISTS incidents")
