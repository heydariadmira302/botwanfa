"""Add global deployment drain control."""

import sqlalchemy as sa
from alembic import op

revision = "0004_deployment_drain"
down_revision = "0003_message_buttons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "deployment_control" not in inspector.get_table_names():
        op.create_table(
            "deployment_control",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column(
                "draining", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("requested_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "outbox_start_id", sa.BigInteger(), nullable=False, server_default="0"
            ),
            sa.Column("ready_notified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    op.execute(
        """
        INSERT INTO deployment_control (
            id, draining, generation, outbox_start_id, updated_at
        )
        VALUES (1, false, 0, 0, now())
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("deployment_control")
