"""monitoring schema

Revision ID: 0002_monitoring
Revises: 0001_initial
Create Date: 2025-10-31 00:00:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_monitoring"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE check_results (
            id BIGSERIAL PRIMARY KEY,
            application_id BIGINT NOT NULL
                REFERENCES applications(id) ON DELETE CASCADE,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status VARCHAR(20) NOT NULL,
            http_status INTEGER,
            latency_ms INTEGER,
            failure_reason VARCHAR(64),
            ssl_expires_at TIMESTAMPTZ,
            ssl_issuer VARCHAR(255),
            final_url VARCHAR(2048),
            redirect_count SMALLINT NOT NULL DEFAULT 0
        )
    """)
    op.execute(
        "CREATE INDEX ix_check_results_app_checked "
        "ON check_results (application_id, checked_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_check_results_checked_at ON check_results (checked_at)"
    )

    op.execute("""
        CREATE TABLE application_monitoring (
            application_id BIGINT PRIMARY KEY
                REFERENCES applications(id) ON DELETE CASCADE,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            interval_seconds INTEGER NOT NULL DEFAULT 300,
            failure_threshold INTEGER NOT NULL DEFAULT 3,
            last_checked_at TIMESTAMPTZ,
            next_check_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_app_monitoring_due "
        "ON application_monitoring (next_check_at) "
        "WHERE enabled = TRUE"
    )

    op.execute("""
        CREATE TABLE application_health_state (
            application_id BIGINT PRIMARY KEY
                REFERENCES applications(id) ON DELETE CASCADE,
            current_status VARCHAR(20) NOT NULL DEFAULT 'unknown',
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_success_at TIMESTAMPTZ,
            last_failure_at TIMESTAMPTZ,
            last_result_at TIMESTAMPTZ
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS application_health_state")
    op.execute("DROP INDEX IF EXISTS ix_app_monitoring_due")
    op.execute("DROP TABLE IF EXISTS application_monitoring")
    op.execute("DROP INDEX IF EXISTS ix_check_results_checked_at")
    op.execute("DROP INDEX IF EXISTS ix_check_results_app_checked")
    op.execute("DROP TABLE IF EXISTS check_results")
