"""Add settings used by the private super-admin console."""

import sqlalchemy as sa
from alembic import op

revision = "0002_admin_controls"
down_revision = "0001_async_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "game_settings",
        sa.Column("checkin_min", sa.Numeric(20, 2), nullable=False, server_default="0.10"),
    )
    op.add_column(
        "game_settings",
        sa.Column("checkin_max", sa.Numeric(20, 2), nullable=False, server_default="0.50"),
    )
    op.add_column(
        "game_settings",
        sa.Column("checkin_step", sa.Numeric(20, 2), nullable=False, server_default="0.10"),
    )
    op.add_column(
        "game_settings",
        sa.Column("streak_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "game_settings",
        sa.Column(
            "streak_rewards",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{\"3\": \"10.00\", \"5\": \"30.00\", \"10\": \"100.00\"}'::json"),
        ),
    )
    op.add_column(
        "game_settings",
        sa.Column("test_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("game_settings", "test_mode")
    op.drop_column("game_settings", "streak_rewards")
    op.drop_column("game_settings", "streak_enabled")
    op.drop_column("game_settings", "checkin_step")
    op.drop_column("game_settings", "checkin_max")
    op.drop_column("game_settings", "checkin_min")
