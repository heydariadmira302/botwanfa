"""Add settings used by the private super-admin console."""

import sqlalchemy as sa
from alembic import op

revision = "0002_admin_controls"
down_revision = "0001_async_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("game_settings")
    }
    columns = (
        sa.Column("checkin_min", sa.Numeric(20, 2), nullable=False, server_default="0.10"),
        sa.Column("checkin_max", sa.Numeric(20, 2), nullable=False, server_default="0.50"),
        sa.Column("checkin_step", sa.Numeric(20, 2), nullable=False, server_default="0.10"),
        sa.Column("streak_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "streak_rewards",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{\"3\": \"10.00\", \"5\": \"30.00\", \"10\": \"100.00\"}'::json"),
        ),
        sa.Column("test_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("game_settings", column)


def downgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("game_settings")
    }
    for name in (
        "test_mode",
        "streak_rewards",
        "streak_enabled",
        "checkin_step",
        "checkin_max",
        "checkin_min",
    ):
        if name in existing:
            op.drop_column("game_settings", name)
