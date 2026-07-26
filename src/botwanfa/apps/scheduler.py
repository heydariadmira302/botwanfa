import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import func, select

from botwanfa.config import get_settings
from botwanfa.db.models import BetBatch, GameSettings, OutboxMessage, Round, TelegramGroup
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


def choose_player_dice_candidate(batches, threshold: Decimal) -> int | None:
    totals: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))
    reached_at: dict[int, tuple[datetime, int]] = {}
    for batch in batches:
        previous = totals[batch.user_id]
        totals[batch.user_id] += batch.total_amount
        if previous < threshold <= totals[batch.user_id] and batch.user_id not in reached_at:
            reached_at[batch.user_id] = (batch.created_at, batch.id)
    candidates = [user_id for user_id, total in totals.items() if total >= threshold]
    if not candidates:
        return None
    return min(candidates, key=lambda user_id: (-totals[user_id], reached_at[user_id]))


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
                unsent = await session.scalar(
                    select(func.count(OutboxMessage.id)).where(
                        OutboxMessage.group_id == group.id,
                        OutboxMessage.status.in_(("pending", "processing")),
                    )
                )
                if unsent:
                    continue
                next_round_seconds = (
                    min(settings.next_round_seconds, 5)
                    if settings.test_mode
                    else settings.next_round_seconds
                )
                if (
                    last_round
                    and last_round.completed_at
                    and last_round.completed_at + timedelta(seconds=next_round_seconds)
                    > now
                ):
                    continue
                last_number = await session.scalar(
                    select(func.coalesce(func.max(Round.round_number), 0)).where(
                        Round.group_id == group.id
                    )
                )
                betting_seconds = (
                    min(settings.betting_seconds, 10)
                    if settings.test_mode
                    else settings.betting_seconds
                )
                round_ = Round(
                    group_id=group.id,
                    round_number=int(last_number or 0) + 1,
                    status=RoundStatus.BETTING.value,
                    betting_opens_at=now,
                    betting_closes_at=now + timedelta(seconds=betting_seconds),
                    settings_snapshot={
                        "minimum_bet": str(settings.minimum_bet),
                        "betting_seconds": betting_seconds,
                        "rolling_seconds": settings.rolling_seconds,
                        "next_round_seconds": settings.next_round_seconds,
                        "player_dice_seconds": settings.player_dice_seconds,
                        "player_dice_threshold": (
                            str(settings.player_dice_threshold)
                            if settings.player_dice_threshold is not None
                            else None
                        ),
                        "history_size": settings.history_size,
                        "test_mode": settings.test_mode,
                    },
                )
                session.add(round_)
                await session.flush()
                session.add(
                    OutboxMessage(
                        group_id=group.id,
                        sequence=10,
                        message_type="round_open",
                        payload={
                            "round_id": round_.id,
                            "round_number": round_.round_number,
                            "betting_seconds": betting_seconds,
                            "minimum_bet": str(settings.minimum_bet),
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
                        sequence=20,
                        message_type="round_closed",
                        payload={"round_id": active.id, "round_number": active.round_number},
                        idempotency_key=f"round:{active.id}:closed",
                    )
                )
                threshold_text = active.settings_snapshot.get("player_dice_threshold")
                if threshold_text is not None:
                    batches = (
                        await session.scalars(
                            select(BetBatch)
                            .where(BetBatch.round_id == active.id)
                            .order_by(BetBatch.created_at, BetBatch.id)
                        )
                    ).all()
                    candidate = choose_player_dice_candidate(batches, Decimal(threshold_text))
                    if candidate is not None:
                        configured_player_seconds = int(
                            active.settings_snapshot.get(
                                "player_dice_seconds", settings.player_dice_seconds
                            )
                        )
                        player_seconds = (
                            min(configured_player_seconds, 5)
                            if active.settings_snapshot.get("test_mode")
                            else configured_player_seconds
                        )
                        active.status = RoundStatus.WAITING_FOR_PLAYER_DICE.value
                        active.settings_snapshot = {
                            **active.settings_snapshot,
                            "player_dice_user_id": candidate,
                            "player_dice_seconds": player_seconds,
                            "player_dice_deadline": None,
                            "player_dice_values": [],
                            "player_dice_message_ids": [],
                        }
                        session.add(
                            OutboxMessage(
                                group_id=group.id,
                                sequence=25,
                                message_type="player_dice_invite",
                                payload={
                                    "round_id": active.id,
                                    "round_number": active.round_number,
                                    "user_id": candidate,
                                    "seconds": player_seconds,
                                },
                                idempotency_key=f"round:{active.id}:player-dice-invite",
                            )
                        )
            elif active.status == RoundStatus.WAITING_FOR_PLAYER_DICE.value:
                deadline_text = active.settings_snapshot.get("player_dice_deadline")
                if not deadline_text:
                    continue
                deadline = datetime.fromisoformat(deadline_text)
                if deadline <= now:
                    active.status = RoundStatus.BOT_ROLLING.value
                    session.add(
                        OutboxMessage(
                            group_id=group.id,
                            sequence=30,
                            message_type="dice_round",
                            payload={"round_id": active.id, "round_number": active.round_number},
                            idempotency_key=f"round:{active.id}:dice",
                        )
                    )
            elif (
                active.status == RoundStatus.CLOSED.value
                and active.betting_closes_at
                and active.betting_closes_at
                + timedelta(
                    seconds=(
                        min(
                            int(
                                active.settings_snapshot.get(
                                    "rolling_seconds", settings.rolling_seconds
                                )
                            ),
                            3,
                        )
                        if active.settings_snapshot.get("test_mode")
                        else int(
                            active.settings_snapshot.get(
                                "rolling_seconds", settings.rolling_seconds
                            )
                        )
                    )
                )
                <= now
            ):
                active.status = RoundStatus.BOT_ROLLING.value
                session.add(
                    OutboxMessage(
                        group_id=group.id,
                        sequence=30,
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
