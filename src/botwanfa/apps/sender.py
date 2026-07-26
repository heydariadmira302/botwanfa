import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape

import structlog
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import BufferedInputFile, InputMediaPhoto
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased

from botwanfa.config import get_settings
from botwanfa.db.models import (
    Bet,
    BetBatch,
    DiceResult,
    OutboxMessage,
    Round,
    RoundPlayerSettlement,
    StreakReward,
    User,
)
from botwanfa.db.session import create_engine_and_session
from botwanfa.domain.dice import evaluate_dice
from botwanfa.domain.state_machine import RoundStatus
from botwanfa.logging import configure_logging
from botwanfa.presentation import (
    BetSummary,
    SettlementSummary,
    TrendPoint,
    bet_item_text,
    closed_bet_text,
    closed_caption,
    load_status_animation,
    open_caption,
    render_bet_summary_pages,
    render_settlement_pages,
    render_trend_image,
    result_caption,
    settlement_text,
)

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


async def send_photo_pages(
    bot: Bot,
    group_id: int,
    pages: list[bytes],
    *,
    caption: str,
    filename_prefix: str,
) -> None:
    for chunk_start in range(0, len(pages), 10):
        chunk = pages[chunk_start : chunk_start + 10]
        if len(chunk) == 1:
            await bot.send_photo(
                group_id,
                BufferedInputFile(
                    chunk[0], filename=f"{filename_prefix}-{chunk_start + 1}.png"
                ),
                caption=caption if chunk_start == 0 else None,
            )
            continue
        media = []
        for index, content in enumerate(chunk):
            absolute_index = chunk_start + index
            media.append(
                InputMediaPhoto(
                    media=BufferedInputFile(
                        content, filename=f"{filename_prefix}-{absolute_index + 1}.png"
                    ),
                    caption=caption if absolute_index == 0 else None,
                )
            )
        await bot.send_media_group(group_id, media=media)


async def send_round_open(bot: Bot, group_id: int, payload: dict) -> None:
    caption = open_caption(
        round_number=int(payload["round_number"]),
        betting_seconds=int(payload["betting_seconds"]),
        minimum_bet=Decimal(payload["minimum_bet"]),
    )
    await bot.send_animation(
        group_id,
        BufferedInputFile(load_status_animation("open"), filename="betting-open.gif"),
        caption=caption,
    )


async def load_bet_summaries(
    session_factory, round_id: int
) -> tuple[list[BetSummary], int, Decimal]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    User.id,
                    User.display_name,
                    Bet.bet_type,
                    Bet.bet_value,
                    Bet.amount,
                )
                .join(BetBatch, BetBatch.user_id == User.id)
                .join(Bet, Bet.batch_id == BetBatch.id)
                .where(BetBatch.round_id == round_id)
                .order_by(BetBatch.created_at, Bet.id)
            )
        ).all()
    grouped: dict[int, dict] = {}
    for user_id, display_name, bet_type, bet_value, amount in rows:
        player = grouped.setdefault(
            user_id,
            {
                "name": display_name,
                "items": defaultdict(lambda: Decimal("0.00")),
                "total": Decimal("0.00"),
            },
        )
        player["items"][(bet_type, bet_value)] += amount
        player["total"] += amount
    summaries = [
        BetSummary(
            user_id=user_id,
            display_name=player["name"],
            items=tuple(
                bet_item_text(bet_type, bet_value, amount)
                for (bet_type, bet_value), amount in player["items"].items()
            ),
            total=player["total"],
        )
        for user_id, player in grouped.items()
    ]
    return summaries, len(rows), sum((item.total for item in summaries), Decimal("0.00"))


async def send_round_closed(
    bot: Bot, session_factory, group_id: int, payload: dict
) -> None:
    round_id = int(payload["round_id"])
    round_number = int(payload["round_number"])
    summaries, bet_count, turnover = await load_bet_summaries(session_factory, round_id)
    async with session_factory() as session:
        round_ = await session.get(Round, round_id)
    rolling_seconds = int(
        (round_.settings_snapshot if round_ else {}).get("rolling_seconds", 10)
    )
    caption = closed_caption(
        round_number=round_number,
        player_count=len(summaries),
        bet_count=bet_count,
        turnover=turnover,
    )
    full_text = closed_bet_text(
        round_number=round_number,
        rows=summaries,
        bet_count=bet_count,
        turnover=turnover,
        rolling_seconds=rolling_seconds,
    )
    if len(full_text) <= 900:
        await bot.send_animation(
            group_id,
            BufferedInputFile(load_status_animation("closed"), filename="betting-closed.gif"),
            caption=full_text,
        )
        return
    await bot.send_animation(
        group_id,
        BufferedInputFile(load_status_animation("closed"), filename="betting-closed.gif"),
        caption=caption,
    )
    if len(full_text) <= 3900:
        await bot.send_message(group_id, full_text)
        return
    summary_pages = await asyncio.to_thread(render_bet_summary_pages, round_number, summaries)
    await send_photo_pages(
        bot,
        group_id,
        summary_pages,
        caption="<b>本期下注清单</b>",
        filename_prefix=f"round-{round_number}-closed",
    )


async def send_player_dice_invite(
    bot: Bot, session_factory, group_id: int, payload: dict
) -> None:
    round_id = int(payload["round_id"])
    user_id = int(payload["user_id"])
    seconds = int(payload["seconds"])
    async with session_factory() as session:
        user = await session.get(User, user_id)
    display_name = user.display_name if user else f"玩家 {user_id}"
    await bot.send_message(
        group_id,
        f"🎲 <a href=\"tg://user?id={user_id}\">{escape(display_name)}</a>"
        f"（ID: <code>{user_id}</code>）已达到本期掷骰门槛。\n"
        f"请在 <b>{seconds} 秒</b>内连续发送三颗 Telegram 原生骰子。",
    )
    async with session_factory() as session, session.begin():
        round_ = await session.get(Round, round_id, with_for_update=True)
        if round_ and round_.status == RoundStatus.WAITING_FOR_PLAYER_DICE.value:
            round_.settings_snapshot = {
                **round_.settings_snapshot,
                "player_dice_deadline": (
                    datetime.now(UTC) + timedelta(seconds=seconds)
                ).isoformat(),
            }


async def send_trend_result(
    bot: Bot, session_factory, group_id: int, payload: dict
) -> None:
    round_id = int(payload["round_id"])
    round_number = int(payload["round_number"])
    async with session_factory() as session:
        current_round = await session.get(Round, round_id)
        current_dice = await session.get(DiceResult, round_id)
        if current_round is None or current_dice is None:
            raise RuntimeError("开奖期次或骰子结果不存在")
        history_size = int(current_round.settings_snapshot.get("history_size", 84))
        history = (
            await session.execute(
                select(Round, DiceResult)
                .join(DiceResult, DiceResult.round_id == Round.id)
                .where(
                    Round.group_id == group_id,
                    Round.status == RoundStatus.COMPLETED.value,
                    Round.round_number <= round_number,
                )
                .order_by(Round.round_number.desc())
                .limit(history_size)
            )
        ).all()
    points = []
    for history_round, dice in reversed(history):
        outcome = evaluate_dice((dice.die_1, dice.die_2, dice.die_3))
        points.append(
            TrendPoint(
                round_number=history_round.round_number,
                dice=outcome.dice,
                total=outcome.total,
                is_big=outcome.is_big,
                is_odd=outcome.is_odd,
                is_straight=outcome.is_straight,
                is_triple=outcome.is_triple,
            )
        )
    current_outcome = evaluate_dice(
        (current_dice.die_1, current_dice.die_2, current_dice.die_3)
    )
    image = await asyncio.to_thread(render_trend_image, points, round_number)
    await bot.send_photo(
        group_id,
        BufferedInputFile(image, filename=f"round-{round_number}-trend.png"),
        caption=result_caption(round_number, current_outcome, current_dice.source),
    )


async def load_settlement_summaries(
    session_factory, round_id: int
) -> list[SettlementSummary]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(RoundPlayerSettlement, User, StreakReward.reward)
                .join(User, User.id == RoundPlayerSettlement.user_id)
                .outerjoin(
                    StreakReward,
                    (StreakReward.round_id == RoundPlayerSettlement.round_id)
                    & (StreakReward.user_id == RoundPlayerSettlement.user_id),
                )
                .where(RoundPlayerSettlement.round_id == round_id)
                .order_by(RoundPlayerSettlement.id)
            )
        ).all()
    return [
        SettlementSummary(
            user_id=settlement.user_id,
            display_name=user.display_name,
            wagered=settlement.wagered,
            returned=settlement.returned,
            net=settlement.net,
            balance=settlement.balance_after,
            streak_reward=reward or Decimal("0.00"),
        )
        for settlement, user, reward in rows
    ]


async def send_settlement_summary(
    bot: Bot, session_factory, group_id: int, payload: dict
) -> None:
    round_id = int(payload["round_id"])
    round_number = int(payload["round_number"])
    rows = await load_settlement_summaries(session_factory, round_id)
    text = settlement_text(round_number, rows)
    if len(text) <= 3900:
        await bot.send_message(group_id, text)
        return
    pages = await asyncio.to_thread(render_settlement_pages, round_number, rows)
    await send_photo_pages(
        bot,
        group_id,
        pages,
        caption=f"<b>第 {round_number} 期 · 全部玩家结算</b>",
        filename_prefix=f"round-{round_number}-settlement",
    )


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
            elif row.message_type == "player_dice_invite":
                round_id = int(row.payload["round_id"])
                round_ = await session.get(Round, round_id)
                if round_ and round_.status == RoundStatus.WAITING_FOR_PLAYER_DICE.value:
                    round_.settings_snapshot = {
                        **round_.settings_snapshot,
                        "player_dice_deadline": (
                            datetime.now(UTC)
                            + timedelta(seconds=int(row.payload["seconds"]))
                        ).isoformat(),
                    }
                row.status = "sent"
                row.sent_at = datetime.now(UTC)
                continue
            row.status = "failed"
            row.last_error = "sender stopped during an external send; held to prevent duplication"


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine, factory = create_engine_and_session(settings.database_url)
    bot = Bot(
        settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
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
                    sent = await bot.send_message(group_id, payload["text"])
                    if payload.get("pin"):
                        try:
                            await bot.pin_chat_message(
                                group_id, sent.message_id, disable_notification=True
                            )
                        except TelegramBadRequest as exc:
                            log.warning("pin_rules_failed", group_id=group_id, error=str(exc))
                elif message_type == "round_open":
                    await send_round_open(bot, group_id, payload)
                elif message_type == "round_closed":
                    await send_round_closed(bot, factory, group_id, payload)
                elif message_type == "player_dice_invite":
                    await send_player_dice_invite(bot, factory, group_id, payload)
                elif message_type == "dice_round":
                    await send_dice_round(bot, factory, message_id, group_id, payload)
                elif message_type == "trend_result":
                    await send_trend_result(bot, factory, group_id, payload)
                elif message_type == "settlement_summary":
                    await send_settlement_summary(bot, factory, group_id, payload)
                else:
                    raise ValueError(f"unknown outbox message type: {message_type}")
            except TelegramRetryAfter as exc:
                await mark_retry(factory, message_id, int(exc.retry_after) + 1, str(exc))
            except (TelegramNetworkError, TelegramServerError) as exc:
                await mark_retry(factory, message_id, 5, str(exc))
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
