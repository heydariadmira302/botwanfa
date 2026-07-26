"""Add configurable template buttons and enable player dice by default."""

import sqlalchemy as sa
from alembic import op

revision = "0003_message_buttons"
down_revision = "0002_admin_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("game_settings")
    }
    if "message_buttons" not in existing:
        op.add_column(
            "game_settings",
            sa.Column(
                "message_buttons",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'::json"),
            ),
        )
    else:
        op.alter_column(
            "game_settings", "message_buttons", server_default=sa.text("'{}'::json")
        )
    op.execute(
        "UPDATE game_settings SET player_dice_seconds = 25 "
        "WHERE player_dice_seconds = 10"
    )
    op.execute(
        "UPDATE game_settings SET player_dice_threshold = 0.01 "
        "WHERE player_dice_threshold = 999999999.00"
    )
    op.alter_column("game_settings", "player_dice_seconds", server_default="25")
    op.alter_column("game_settings", "player_dice_threshold", server_default="0.01")


def downgrade() -> None:
    op.alter_column("game_settings", "player_dice_threshold", server_default=None)
    op.alter_column("game_settings", "player_dice_seconds", server_default=None)
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("game_settings")
    }
    if "message_buttons" in existing:
        op.drop_column("game_settings", "message_buttons")
