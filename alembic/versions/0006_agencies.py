"""introduce agencies and link clients

Revision ID: 0006_agencies
Revises: 0005_backups
Create Date: 2026-11-04 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_agencies"
down_revision: Union[str, None] = "0005_backups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create the agencies table
    op.create_table(
        "agencies",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )

    # 2. Insert the Default Agency
    op.execute("INSERT INTO agencies (name) VALUES ('Default Agency')")

    # 3. Add agency_id to clients (nullable initially)
    op.add_column(
        "clients",
        sa.Column("agency_id", sa.BigInteger, nullable=True),
    )

    # 4. Backfill existing clients to the Default Agency (id=1)
    op.execute("UPDATE clients SET agency_id = 1")

    # 5. Make agency_id NOT NULL
    op.alter_column("clients", "agency_id", nullable=False)

    # 6. Add Foreign Key constraint with RESTRICT
    op.create_foreign_key(
        "fk_clients_agency_id",
        "clients",
        "agencies",
        ["agency_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    
    # 7. Add Index
    op.create_index("ix_clients_agency_id", "clients", ["agency_id"])


def downgrade() -> None:
    op.drop_index("ix_clients_agency_id", table_name="clients")
    op.drop_constraint("fk_clients_agency_id", "clients", type_="foreignkey")
    op.drop_column("clients", "agency_id")
    op.drop_table("agencies")
