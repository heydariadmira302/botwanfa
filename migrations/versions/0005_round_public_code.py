"""Store and index the public round code used in group messages."""

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0005_round_public_code"
down_revision = "0004_deployment_drain"
branch_labels = None
depends_on = None


def _public_code(group_id: int, round_number: int) -> str:
    value = f"{group_id}:{round_number}".encode("ascii")
    return hashlib.blake2s(value, digest_size=16, person=b"bwfround").hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("rounds")}
    if "public_code" not in columns:
        op.add_column(
            "rounds", sa.Column("public_code", sa.String(length=32), nullable=True)
        )

    pending: list[dict[str, int | str]] = []
    rows = bind.execute(
        sa.text(
            "SELECT id, group_id, round_number FROM rounds "
            "WHERE public_code IS NULL ORDER BY id"
        )
    )
    for row in rows:
        pending.append(
            {
                "id": int(row.id),
                "public_code": _public_code(int(row.group_id), int(row.round_number)),
            }
        )
        if len(pending) >= 1000:
            bind.execute(
                sa.text(
                    "UPDATE rounds SET public_code = :public_code WHERE id = :id"
                ),
                pending,
            )
            pending.clear()
    if pending:
        bind.execute(
            sa.text("UPDATE rounds SET public_code = :public_code WHERE id = :id"),
            pending,
        )

    op.alter_column("rounds", "public_code", nullable=False)
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("rounds")}
    if "uq_rounds_public_code" not in indexes:
        op.create_index(
            "uq_rounds_public_code", "rounds", ["public_code"], unique=True
        )


def downgrade() -> None:
    indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("rounds")
    }
    if "uq_rounds_public_code" in indexes:
        op.drop_index("uq_rounds_public_code", table_name="rounds")
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("rounds")
    }
    if "public_code" in columns:
        op.drop_column("rounds", "public_code")
