import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select

from botwanfa.config import get_settings
from botwanfa.db.models import GameSettings, OutboxMessage, Round, TelegramGroup
from botwanfa.db.session import create_engine_and_session
from botwanfa.domain.state_machine import RoundStatus
from botwanfa.logging import configure_logging

log = structlog.get_logger()
ACTIVE = [
    RoundStatus.WAITING.value,
    RoundStatus.BETTING.value,
    RoundStatus.CLOSED.value,
    RoundStatus.WAITING_FOR_PLAYER_DICE.value,
    RoundStatus.BOT_ROLLING.value,
    RoundStatus.SETTLING.value,
    RoundStatus.PAUSED.value,
    RoundStatus.FAILED.value,
    RoundStatus.MANUAL_REVIEW.value,
]


async def tick(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        groups = (
            await session.scalars(
                select(TelegramGroup).where(
                    TelegramGroup.enabled.is_(True), TelegramGroup.paused.is_(False)
                )
            )
        ).all()
        for group in groups:
            locked = await session.scalar(select(func.pg_try_advisory_xact_lock(group.id)))
            if not locked:
                continue
            active = await session.scalar(
                select(Round)
                .where(Round.group_id == group.id, Round.status.in_(ACTIVE))
                .with_for_update()
            )
            settings = await session.get(GameSettings, group.id)
            if settings is None:
                continue
            if active is None:
                last_round = await session.scalar(
                    select(Round)
                    .where(Round.group_id == group.id)
                    .order_by(Round.round_number.desc())
                    .limit(1)
                )
                if (
                    last_round
                    and last_round.completed_at
                    and last_round.completed_at + timedelta(seconds=settings.next_round_seconds)
                    > now
                ):
                    continue
                last_number = await session.scalar(
                    select(func.coalesce(func.max(Round.round_number), 0)).where(
                        Round.group_id == group.id
                    )
                )
                round_ = Round(
                    group_id=group.id,
                    round_number=int(last_number or 0) + 1,
                    status=RoundStatus.BETTING.value,
                    betting_opens_at=now,
                    betting_closes_at=now + timedelta(seconds=settings.betting_seconds),
                    settings_snapshot={
                        "minimum_bet": str(settings.minimum_bet),
                        "betting_seconds": settings.betting_seconds,
                    },
                )
                session.add(round_)
                await session.flush()
                session.add(
                    OutboxMessage(
                        group_id=group.id,
                        sequence=1,
                        message_type="text",
                        payload={
                            "text": f"🎲 第 {round_.round_number} 期开始下注，时间 {settings.betting_seconds} 秒。"
                        },
                        idempotency_key=f"round:{round_.id}:open",
                    )
                )
            elif (
                active.status == RoundStatus.BETTING.value
                and active.betting_closes_at
                and active.betting_closes_at <= now
            ):
                active.status = RoundStatus.CLOSED.value
                session.add(
                    OutboxMessage(
                        group_id=group.id,
                        sequence=2,
                        message_type="text",
                        payload={"text": f"🔒 第 {active.round_number} 期已封盘。"},
                        idempotency_key=f"round:{active.id}:closed",
                    )
                )
            elif active.status == RoundStatus.CLOSED.value:
                active.status = RoundStatus.BOT_ROLLING.value
                session.add(
                    OutboxMessage(
                        group_id=group.id,
                        sequence=3,
                        message_type="dice_round",
                        payload={"round_id": active.id, "round_number": active.round_number},
                        idempotency_key=f"round:{active.id}:dice",
                    )
                )


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine, factory = create_engine_and_session(settings.database_url)
    try:
        while True:
            try:
                await tick(factory)
            except Exception:
                log.exception("scheduler_tick_failed")
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run())
