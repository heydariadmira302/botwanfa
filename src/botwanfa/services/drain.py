from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import BigInteger, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from botwanfa.db.models import DeploymentControl, OutboxMessage, Round, TelegramGroup
from botwanfa.domain.state_machine import RoundStatus

ROUND_OUTBOX_TYPES = (
    "round_open",
    "round_closed",
    "player_dice_invite",
    "player_dice_ack",
    "dice_round",
    "round_result",
    "trend_result",
    "settlement_summary",
)


@dataclass(frozen=True)
class DrainProgress:
    active_rounds: int = 0
    betting_rounds: int = 0
    waiting_player_rounds: int = 0
    rolling_rounds: int = 0
    settling_rounds: int = 0
    blocked_rounds: int = 0
    pending_round_messages: int = 0
    failed_round_messages: int = 0

    @property
    def ready(self) -> bool:
        return (
            self.active_rounds == 0
            and self.pending_round_messages == 0
            and self.failed_round_messages == 0
        )


async def get_deployment_control(
    session: AsyncSession, *, for_update: bool = False
) -> DeploymentControl:
    statement = select(DeploymentControl).where(DeploymentControl.id == 1)
    if for_update:
        statement = statement.with_for_update()
    control = await session.scalar(statement)
    if control is None:
        control = DeploymentControl(id=1)
        session.add(control)
        await session.flush()
    return control


async def set_draining(
    session: AsyncSession, enabled: bool, *, requested_by: int | None
) -> DeploymentControl:
    control = await get_deployment_control(session, for_update=True)
    now = datetime.now(UTC)
    if enabled and not control.draining:
        control.draining = True
        control.generation += 1
        control.requested_at = now
        control.requested_by = requested_by
        control.outbox_start_id = int(
            await session.scalar(select(func.coalesce(func.max(OutboxMessage.id), 0)))
            or 0
        )
        control.ready_notified_at = None
    elif not enabled and control.draining:
        control.draining = False
        control.requested_at = None
        control.requested_by = None
        control.outbox_start_id = 0
        control.ready_notified_at = None
    control.updated_at = now
    return control


async def load_drain_progress(
    session: AsyncSession,
    *,
    failed_after_id: int | None = None,
    drain_started_at: datetime | None = None,
) -> DrainProgress:
    rows = (
        await session.execute(
            select(Round.status, func.count(Round.id))
            .where(Round.status != RoundStatus.COMPLETED.value)
            .group_by(Round.status)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    active_rounds = sum(counts.values())
    rolling_rounds = sum(
        counts.get(status, 0)
        for status in (RoundStatus.CLOSED.value, RoundStatus.BOT_ROLLING.value)
    )
    blocked_rounds = int(
        await session.scalar(
            select(func.count(Round.id))
            .join(TelegramGroup, TelegramGroup.id == Round.group_id)
            .where(
                Round.status != RoundStatus.COMPLETED.value,
                or_(
                    TelegramGroup.enabled.is_(False),
                    TelegramGroup.paused.is_(True),
                    Round.status.in_(
                        (
                            RoundStatus.WAITING.value,
                            RoundStatus.PAUSED.value,
                            RoundStatus.FAILED.value,
                            RoundStatus.MANUAL_REVIEW.value,
                        )
                    ),
                ),
            )
        )
        or 0
    )
    pending_round_messages = int(
        await session.scalar(
            select(func.count(OutboxMessage.id)).where(
                OutboxMessage.message_type.in_(ROUND_OUTBOX_TYPES),
                OutboxMessage.status.in_(("pending", "processing")),
            )
        )
        or 0
    )
    failed_query = select(func.count(OutboxMessage.id)).where(
        OutboxMessage.message_type.in_(ROUND_OUTBOX_TYPES),
        OutboxMessage.status == "failed",
    )
    if failed_after_id is not None and drain_started_at is not None:
        current_round_failure = exists(
            select(Round.id).where(
                Round.id
                == cast(OutboxMessage.payload["round_id"].as_string(), BigInteger),
                or_(
                    Round.completed_at.is_(None),
                    Round.completed_at >= drain_started_at,
                ),
            )
        )
        failed_query = failed_query.where(
            or_(OutboxMessage.id > failed_after_id, current_round_failure)
        )
    elif failed_after_id is not None:
        failed_query = failed_query.where(OutboxMessage.id > failed_after_id)
    failed_round_messages = int(await session.scalar(failed_query) or 0)
    return DrainProgress(
        active_rounds=active_rounds,
        betting_rounds=counts.get(RoundStatus.BETTING.value, 0),
        waiting_player_rounds=counts.get(RoundStatus.WAITING_FOR_PLAYER_DICE.value, 0),
        rolling_rounds=rolling_rounds,
        settling_rounds=counts.get(RoundStatus.SETTLING.value, 0),
        blocked_rounds=blocked_rounds,
        pending_round_messages=pending_round_messages,
        failed_round_messages=failed_round_messages,
    )
