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
                        "test_mode": settings.test_mode,
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
                            "text": f"🎲 第 {round_.round_number} 期开始下注，时间 {betting_seconds} 秒。"
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
                if settings.player_dice_threshold is not None:
                    batches = (
                        await session.scalars(
                            select(BetBatch)
                            .where(BetBatch.round_id == active.id)
                            .order_by(BetBatch.created_at, BetBatch.id)
                        )
                    ).all()
                    candidate = choose_player_dice_candidate(
                        batches, settings.player_dice_threshold
                    )
                    if candidate is not None:
                        player_seconds = (
                            min(settings.player_dice_seconds, 5)
                            if settings.test_mode
                            else settings.player_dice_seconds
                        )
                        active.status = RoundStatus.WAITING_FOR_PLAYER_DICE.value
                        active.settings_snapshot = {
                            **active.settings_snapshot,
                            "player_dice_user_id": candidate,
                            "player_dice_deadline": (now + timedelta(seconds=player_seconds)).isoformat(),
                            "player_dice_values": [],
                            "player_dice_message_ids": [],
                        }
                        session.add(
                            OutboxMessage(
                                group_id=group.id,
                                sequence=3,
                                message_type="text",
                                payload={
                                    "text": (
                                        f"🎲 玩家 ID {candidate} 已达到本期掷骰门槛，"
                                        f"请在 {player_seconds} 秒内发送三颗原生骰子。"
                                    )
                                },
                                idempotency_key=f"round:{active.id}:player-dice-invite",
                            )
                        )
            elif active.status == RoundStatus.WAITING_FOR_PLAYER_DICE.value:
                deadline_text = active.settings_snapshot.get("player_dice_deadline")
                deadline = datetime.fromisoformat(deadline_text) if deadline_text else now
                if deadline <= now:
                    active.status = RoundStatus.BOT_ROLLING.value
                    session.add(
                        OutboxMessage(
                            group_id=group.id,
                            sequence=4,
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
                        min(settings.rolling_seconds, 3)
                        if settings.test_mode
                        else settings.rolling_seconds
                    )
                )
                <= now
            ):
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
