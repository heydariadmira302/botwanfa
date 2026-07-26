"""Create the asynchronous multi-group core schema."""

from alembic import op

from botwanfa.db import models  # noqa: F401
from botwanfa.db.base import Base

revision = "0001_async_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_rounds_one_active_per_group
        ON rounds (group_id)
        WHERE status <> 'completed'
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
