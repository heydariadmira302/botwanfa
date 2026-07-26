import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased

from botwanfa.config import get_settings
from botwanfa.db.models import DiceResult, OutboxMessage, Round
from botwanfa.db.session import create_engine_and_session
from botwanfa.domain.state_machine import RoundStatus
from botwanfa.logging import configure_logging

log = structlog.get_logger()


async def claim_one(session_factory) -> tuple[int, int, str, dict] | None:
    async with session_factory() as session, session.begin():
        earlier = aliased(OutboxMessage)
        row = await session.scalar(
            select(OutboxMessage)
            .where(
                OutboxMessage.status == "pending",
                OutboxMessage.available_at <= datetime.now(UTC),
                ~exists(
                    select(earlier.id).where(
                        earlier.group_id == OutboxMessage.group_id,
                        earlier.id < OutboxMessage.id,
                        earlier.status.in_(("pending", "processing")),
                    )
                ),
            )
            .order_by(OutboxMessage.group_id, OutboxMessage.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None or row.group_id is None:
            return None
        row.status = "processing"
        row.attempt_count += 1
        return row.id, row.group_id, row.message_type, row.payload


async def mark_retry(session_factory, message_id: int, delay: int, error: str) -> None:
    async with session_factory() as session, session.begin():
        row = await session.get(OutboxMessage, message_id, with_for_update=True)
        if row:
            row.status = "pending"
            row.available_at = datetime.now(UTC) + timedelta(seconds=delay)
            row.last_error = error[:2000]


async def mark_sent(session_factory, message_id: int) -> None:
    async with session_factory() as session, session.begin():
        row = await session.get(OutboxMessage, message_id, with_for_update=True)
        if row:
            row.status = "sent"
            row.sent_at = datetime.now(UTC)


async def mark_failed(session_factory, message_id: int, error: str) -> None:
    async with session_factory() as session, session.begin():
        row = await session.get(OutboxMessage, message_id, with_for_update=True)
        if row:
            row.status = "failed"
            row.last_error = error[:2000]
            if row.message_type == "dice_round":
                round_ = await session.get(Round, int(row.payload["round_id"]))
                if round_:
                    round_.status = RoundStatus.MANUAL_REVIEW.value


async def send_dice_round(
    bot: Bot, session_factory, outbox_id: int, group_id: int, payload: dict
) -> None:
    round_id = int(payload["round_id"])
    values = list(payload.get("dice_values", []))
    message_ids = list(payload.get("dice_message_ids", []))
    while len(values) < 3:
        message = await bot.send_dice(group_id, emoji="🎲")
        if message.dice is None:
            raise RuntimeError("Telegram dice response has no value")
        values.append(message.dice.value)
        message_ids.append(message.message_id)
        async with session_factory() as session, session.begin():
            row = await session.get(OutboxMessage, outbox_id, with_for_update=True)
            if row is None:
                raise RuntimeError("outbox row disappeared while sending dice")
            row.payload = {
                **row.payload,
                "dice_values": values.copy(),
                "dice_message_ids": message_ids.copy(),
            }
        await asyncio.sleep(0.4)
    async with session_factory() as session, session.begin():
        round_ = await session.get(Round, round_id, with_for_update=True)
        if round_ is None:
            raise RuntimeError("round disappeared while sending dice")
        if await session.get(DiceResult, round_id) is None:
            session.add(
                DiceResult(
                    round_id=round_id,
                    die_1=values[0],
                    die_2=values[1],
                    die_3=values[2],
                    telegram_message_ids=message_ids,
                    source="bot",
                )
            )
        round_.status = RoundStatus.SETTLING.value


async def recover_interrupted(session_factory) -> None:
    async with session_factory() as session, session.begin():
        rows = (
            await session.scalars(
                select(OutboxMessage)
                .where(OutboxMessage.status == "processing")
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            if row.message_type == "dice_round":
                round_id = int(row.payload["round_id"])
                round_ = await session.get(Round, round_id)
                result = await session.get(DiceResult, round_id)
                if result is not None:
                    row.status = "sent"
                    row.sent_at = datetime.now(UTC)
                    if round_ and round_.status == RoundStatus.BOT_ROLLING.value:
                        round_.status = RoundStatus.SETTLING.value
                    continue
                if round_:
                    round_.status = RoundStatus.MANUAL_REVIEW.value
            row.status = "failed"
            row.last_error = "sender stopped during an external send; held to prevent duplication"


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine, factory = create_engine_and_session(settings.database_url)
    bot = Bot(settings.bot_token.get_secret_value())
    await recover_interrupted(factory)
    try:
        while True:
            item = await claim_one(factory)
            if item is None:
                await asyncio.sleep(settings.sender_poll_seconds)
                continue
            message_id, group_id, message_type, payload = item
            try:
                if message_type == "text":
                    await bot.send_message(group_id, payload["text"])
                elif message_type == "dice_round":
                    await send_dice_round(bot, factory, message_id, group_id, payload)
                else:
                    raise ValueError(f"unknown outbox message type: {message_type}")
            except TelegramRetryAfter as exc:
                await mark_retry(factory, message_id, int(exc.retry_after) + 1, str(exc))
            except Exception as exc:
                await mark_failed(factory, message_id, str(exc))
                log.exception("send_failed", outbox_message_id=message_id)
            else:
                await mark_sent(factory, message_id)
    finally:
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())
