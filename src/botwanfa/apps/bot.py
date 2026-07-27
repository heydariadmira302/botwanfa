import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from html import escape

import structlog
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from botwanfa.apps.admin import admin_menu_markup
from botwanfa.apps.admin import router as admin_router
from botwanfa.config import get_settings
from botwanfa.db.models import (
    BetBatch,
    DailyCheckin,
    DiceResult,
    GameSettings,
    OddsSetting,
    OutboxMessage,
    Round,
    User,
    Wallet,
    WalletLedger,
)
from botwanfa.db.session import create_engine_and_session
from botwanfa.domain.bets import BetParseError, looks_like_bet, parse_bets
from botwanfa.domain.state_machine import RoundStatus
from botwanfa.logging import configure_logging
from botwanfa.presentation import bet_item_text, player_mention, rules_text, success_bet_text
from botwanfa.services.betting import BettingError, BettingService
from botwanfa.services.provisioning import provision_participant

router = Router()
betting = BettingService()
log = structlog.get_logger()
__all__ = ["admin_menu_markup"]
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


def append_player_dice_roll(
    snapshot: dict,
    *,
    message_id: int,
    value: int,
    accepted_at: datetime,
) -> dict | None:
    message_ids = list(snapshot.get("player_dice_message_ids", []))
    if message_id in message_ids or len(message_ids) >= 3:
        return None
    values = list(snapshot.get("player_dice_values", []))
    roll_times = list(snapshot.get("player_dice_times", []))
    message_ids.append(message_id)
    values.append(value)
    roll_times.append(accepted_at.isoformat())
    return {
        **snapshot,
        "player_dice_values": values,
        "player_dice_message_ids": message_ids,
        "player_dice_times": roll_times,
    }


async def ensure_participant(message: Message, session_factory) -> None:
    user = message.from_user
    if user is None or message.chat.type not in GROUP_TYPES:
        return
    async with session_factory() as session, session.begin():
        await provision_participant(
            session,
            group_id=message.chat.id,
            group_title=message.chat.title or "",
            user_id=user.id,
            username=user.username,
            display_name=user.full_name,
        )


@router.message(F.chat.type.in_(GROUP_TYPES), F.new_chat_members)
async def register_new_members(message: Message, session_factory) -> None:
    members = [member for member in message.new_chat_members or [] if not member.is_bot]
    if not members:
        return
    async with session_factory() as session, session.begin():
        for member in members:
            await provision_participant(
                session,
                group_id=message.chat.id,
                group_title=message.chat.title or "",
                user_id=member.id,
                username=member.username,
                display_name=member.full_name,
            )
    mentions = "、".join(player_mention(member.id, member.full_name) for member in members)
    await message.answer(
        f"欢迎 {mentions}\n钱包已创建，当前余额：<b>0.00</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("start"), F.chat.type.in_(GROUP_TYPES))
async def start(message: Message, session_factory) -> None:
    await ensure_participant(message, session_factory)
    await message.reply(
        "<b>BOTWANFA 已连接本群</b>\n\n"
        "机器人会自动开盘、封盘，邀请最高下注者掷骰并在超时后自动补发，随后生成走势图并结算。\n"
        "发送 /玩法 查看完整规则，发送 余额、签到、日榜、周榜或月榜查询个人数据。",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("balance", "余额", "我的"))
async def balance(message: Message, session_factory) -> None:
    user = message.from_user
    if user is None or message.chat.type not in GROUP_TYPES:
        return
    await ensure_participant(message, session_factory)
    async with session_factory() as session:
        wallet = await session.scalar(
            select(Wallet).where(
                Wallet.group_id == message.chat.id,
                Wallet.user_id == user.id,
            )
        )
    await message.reply(
        f"💰 {player_mention(user.id, user.full_name)}\n"
        f"当前余额：<b>{wallet.balance if wallet else 0.00}</b>",
        parse_mode=ParseMode.HTML,
    )


async def send_rules(message: Message, session_factory) -> None:
    await ensure_participant(message, session_factory)
    async with session_factory() as session:
        settings = await session.get(GameSettings, message.chat.id)
        odds_rows = (
            await session.scalars(
                select(OddsSetting).where(
                    OddsSetting.group_id == message.chat.id,
                    OddsSetting.enabled.is_(True),
                )
            )
        ).all()
    if settings is None:
        await message.reply("本群配置尚未建立，请先发送 /start。")
        return
    odds = {(row.bet_type, row.bet_value): row.payout_multiplier for row in odds_rows}
    await message.reply(
        rules_text(odds, settings.minimum_bet),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("rules", "玩法", "规则"), F.chat.type.in_(GROUP_TYPES))
async def rules_command(message: Message, session_factory) -> None:
    await send_rules(message, session_factory)


@router.message(Command("checkin", "签到"), F.chat.type.in_(GROUP_TYPES))
async def checkin_command(message: Message, session_factory) -> None:
    await process_checkin(message, session_factory)


def failure_message(user, *, item: str = "", reason: str) -> str:
    mention = player_mention(user.id, user.full_name)
    item_line = f"\u9519\u8bef\u9879\u76ee\uff1a{escape(item)}\n" if item else ""
    return (
        f"\u274c {mention} \u672c\u6761\u6295\u6ce8\u672a\u53d7\u7406\n"
        f"{item_line}\u539f\u56e0\uff1a{escape(reason)}\n"
        "\u672c\u6761\u6d88\u606f\u4e2d\u7684\u6295\u6ce8\u5747\u672a\u6263\u5206\u3002"
    )


async def process_checkin(message: Message, session_factory) -> None:
    user = message.from_user
    if user is None:
        return
    await ensure_participant(message, session_factory)
    business_date = datetime.now(get_settings().tz).date()
    async with session_factory() as session, session.begin():
        settings = await session.get(GameSettings, message.chat.id)
        wallet = await session.scalar(
            select(Wallet)
            .where(Wallet.group_id == message.chat.id, Wallet.user_id == user.id)
            .with_for_update()
        )
        if settings is None or wallet is None:
            raise RuntimeError("签到资料尚未建立")
        steps = int((settings.checkin_max - settings.checkin_min) / settings.checkin_step)
        reward = settings.checkin_min + settings.checkin_step * secrets.randbelow(steps + 1)
        checkin_id = await session.scalar(
            insert(DailyCheckin)
            .values(
                group_id=message.chat.id,
                user_id=user.id,
                business_date=business_date,
                reward=reward,
            )
            .on_conflict_do_nothing(index_elements=["group_id", "user_id", "business_date"])
            .returning(DailyCheckin.id)
        )
        if checkin_id is None:
            balance = wallet.balance
        else:
            wallet.balance += reward
            balance = wallet.balance
            session.add(
                WalletLedger(
                    wallet_id=wallet.id,
                    idempotency_key=f"checkin:{message.chat.id}:{user.id}:{business_date.isoformat()}",
                    entry_type="checkin_reward",
                    amount=reward,
                    balance_after=wallet.balance,
                    reference_type="daily_checkin",
                    reference_id=checkin_id,
                )
            )
    mention = f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'
    if checkin_id is None:
        text = f"{mention} 今天已经签到过了，当前余额 {balance}。"
    else:
        text = f"✅ {mention} 签到成功，奖励 {reward}，当前余额 {balance}。"
    await message.reply(text, parse_mode=ParseMode.HTML)


def ranking_start(period: str) -> datetime:
    now = datetime.now(get_settings().tz)
    if period == "day":
        local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        local = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:
        local = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


async def send_group_ranking(message: Message, session_factory, period: str) -> None:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    User.id,
                    User.display_name,
                    func.sum(BetBatch.total_amount).label("turnover"),
                )
                .join(User, User.id == BetBatch.user_id)
                .where(
                    BetBatch.group_id == message.chat.id,
                    BetBatch.created_at >= ranking_start(period),
                )
                .group_by(User.id, User.display_name)
                .order_by(func.sum(BetBatch.total_amount).desc())
                .limit(5)
            )
        ).all()
    title = {"day": "日榜", "week": "周榜", "month": "月榜"}[period]
    lines = [
        f"{index}. {player_mention(user_id, name)} · 流水 {turnover}"
        for index, (user_id, name, turnover) in enumerate(rows, 1)
    ]
    text = f"<b>🏆 {title}</b>\n\n" + ("\n".join(lines) if lines else "当前周期暂无有效投注。")
    await message.reply(text, parse_mode=ParseMode.HTML)


@router.message(Command("日榜", "周榜", "月榜"), F.chat.type.in_(GROUP_TYPES))
async def ranking_command(message: Message, session_factory) -> None:
    command = (message.text or "").split("@", 1)[0].lstrip("/")
    period = {"日榜": "day", "周榜": "week", "月榜": "month"}.get(command)
    if period:
        await send_group_ranking(message, session_factory, period)


@router.message(F.chat.type.in_(GROUP_TYPES), F.dice)
async def collect_player_dice(message: Message, session_factory) -> None:
    user = message.from_user
    if user is None or message.dice is None or message.dice.emoji != "🎲":
        return
    accepted_at = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        round_ = await session.scalar(
            select(Round)
            .where(
                Round.group_id == message.chat.id,
                Round.status == RoundStatus.WAITING_FOR_PLAYER_DICE.value,
            )
            .with_for_update()
        )
        if round_ is None:
            return
        snapshot = dict(round_.settings_snapshot)
        if int(snapshot.get("player_dice_user_id", 0)) != user.id:
            return
        deadline_text = snapshot.get("player_dice_deadline")
        if deadline_text:
            deadline = datetime.fromisoformat(deadline_text)
        else:
            deadline = datetime.now(UTC) + timedelta(
                seconds=int(snapshot.get("player_dice_seconds", 25))
            )
            snapshot["player_dice_deadline"] = deadline.isoformat()
        if deadline <= accepted_at:
            return
        updated_snapshot = append_player_dice_roll(
            snapshot,
            message_id=message.message_id,
            value=message.dice.value,
            accepted_at=accepted_at,
        )
        if updated_snapshot is None:
            return
        values = list(updated_snapshot["player_dice_values"])
        message_ids = list(updated_snapshot["player_dice_message_ids"])
        round_.settings_snapshot = updated_snapshot
        roll_time = accepted_at.astimezone(get_settings().tz).strftime("%H:%M:%S")
        session.add(
            OutboxMessage(
                group_id=message.chat.id,
                sequence=26 + len(values),
                message_type="player_dice_ack",
                payload={
                    "round_id": round_.id,
                    "reply_to_message_id": message.message_id,
                    "text": (
                        f"🎲 骰子有效，识别点数为: <b>{message.dice.value}</b>\n"
                        f"摇骰子时间: <code>{roll_time}</code>"
                    ),
                },
                idempotency_key=(
                    f"round:{round_.id}:player-dice-ack:{message.message_id}"
                ),
            )
        )
        if len(values) == 3:
            if await session.get(DiceResult, round_.id) is None:
                session.add(
                    DiceResult(
                        round_id=round_.id,
                        die_1=values[0],
                        die_2=values[1],
                        die_3=values[2],
                        telegram_message_ids=message_ids,
                        source=f"player:{user.id}",
                    )
                )
            round_.status = RoundStatus.SETTLING.value


def betting_unavailable_reason(round_: Round | None, now: datetime) -> str | None:
    if round_ is None:
        return "当前尚未开盘，请等待下一期开始下注"
    if (
        round_.status == RoundStatus.BETTING.value
        and (round_.betting_closes_at is None or now < round_.betting_closes_at)
    ):
        return None
    if round_.status == RoundStatus.BETTING.value:
        return "本期已停止下注，请等待下一期开盘"
    if round_.status == RoundStatus.PAUSED.value:
        return "本群游戏已暂停，暂不受理下注"
    if round_.status in {
        RoundStatus.CLOSED.value,
        RoundStatus.WAITING_FOR_PLAYER_DICE.value,
        RoundStatus.BOT_ROLLING.value,
        RoundStatus.SETTLING.value,
        RoundStatus.COMPLETED.value,
    }:
        return "本期已停止下注，请等待下一期开盘"
    return "当前期次暂不受理下注，请稍后再试"


async def current_betting_unavailable_reason(
    session_factory, group_id: int
) -> str | None:
    async with session_factory() as session:
        latest_round = await session.scalar(
            select(Round)
            .where(Round.group_id == group_id)
            .order_by(Round.round_number.desc())
            .limit(1)
        )
    return betting_unavailable_reason(latest_round, datetime.now(UTC))


@router.message(F.chat.type.in_(GROUP_TYPES), F.text)
async def group_text(message: Message, session_factory) -> None:
    text = message.text or ""
    command = text.strip().lower()
    if command in {"玩法", "规则"}:
        await send_rules(message, session_factory)
        return
    if command in {"余额", "我的"}:
        await balance(message, session_factory)
        return
    if command in {"签到", "qd"}:
        await process_checkin(message, session_factory)
        return
    ranking_period = {"日榜": "day", "周榜": "week", "月榜": "month"}.get(text.strip())
    if ranking_period:
        await send_group_ranking(message, session_factory, ranking_period)
        return
    user = message.from_user
    bet_intent = looks_like_bet(text)
    participant_ready = False
    if user and bet_intent:
        await ensure_participant(message, session_factory)
        participant_ready = True
        unavailable_reason = await current_betting_unavailable_reason(
            session_factory, message.chat.id
        )
        if unavailable_reason:
            await message.reply(
                failure_message(user, reason=unavailable_reason),
                parse_mode=ParseMode.HTML,
            )
            return
    try:
        items = parse_bets(text)
    except BetParseError as exc:
        if user and bet_intent:
            await message.reply(
                failure_message(user, item=exc.item, reason=exc.reason),
                parse_mode=ParseMode.HTML,
            )
        return
    if user is None:
        return
    if not bet_intent:
        await ensure_participant(message, session_factory)
        participant_ready = True
        unavailable_reason = await current_betting_unavailable_reason(
            session_factory, message.chat.id
        )
        if unavailable_reason:
            await message.reply(
                failure_message(user, reason=unavailable_reason),
                parse_mode=ParseMode.HTML,
            )
            return
    if not participant_ready:
        await ensure_participant(message, session_factory)
    try:
        async with session_factory() as session, session.begin():
            result = await betting.place_batch(
                session,
                group_id=message.chat.id,
                user_id=user.id,
                telegram_message_id=message.message_id,
                original_text=text,
                items=items,
            )
    except BettingError as exc:
        await message.reply(
            failure_message(user, reason=str(exc)),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        log.exception(
            "bet_submission_failed",
            group_id=message.chat.id,
            user_id=user.id,
            telegram_message_id=message.message_id,
        )
        await message.reply(
            failure_message(user, reason="系统暂时未完成受理，请重新发送本条下注"),
            parse_mode=ParseMode.HTML,
        )
        return
    if not result.duplicate:
        await message.reply(
            success_bet_text(
                display_name=user.full_name,
                user_id=user.id,
                items=(
                    bet_item_text(item.bet_type.value, item.value or "", item.amount)
                    for item in items
                ),
                total=result.total_amount,
                balance=result.balance_after,
            ),
            parse_mode=ParseMode.HTML,
        )


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    token = settings.bot_token.get_secret_value()
    if not token:
        raise RuntimeError("BOT_TOKEN is empty")
    engine, session_factory = create_engine_and_session(settings.database_url)
    bot = Bot(token)
    dispatcher = Dispatcher()
    dispatcher.include_router(admin_router)
    dispatcher.include_router(router)
    try:
        await dispatcher.start_polling(bot, session_factory=session_factory)
    finally:
        await engine.dispose()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())
