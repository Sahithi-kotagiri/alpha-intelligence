"""rulebook factors on universe snapshots

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("universe_snapshots") as b:
        b.add_column(sa.Column("factors", sa.JSON(), nullable=True))
    with op.batch_alter_table("screen_runs") as b:
        b.add_column(sa.Column("phase", sa.String(length=40), nullable=True))
        b.add_column(sa.Column("rulebook_version", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("screen_runs") as b:
        b.drop_column("rulebook_version")
        b.drop_column("phase")
    with op.batch_alter_table("universe_snapshots") as b:
        b.drop_column("factors")
