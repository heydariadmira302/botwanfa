import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from html import escape

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
    Round,
    User,
    Wallet,
    WalletLedger,
)
from botwanfa.db.session import create_engine_and_session
from botwanfa.domain.bets import BetParseError, parse_bets
from botwanfa.domain.state_machine import RoundStatus
from botwanfa.logging import configure_logging
from botwanfa.services.betting import BettingError, BettingService
from botwanfa.services.provisioning import provision_participant

router = Router()
betting = BettingService()
__all__ = ["admin_menu_markup"]
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
BET_TOKENS = (
    "\u5927",
    "\u5c0f",
    "\u5355",
    "\u53cc",
    "dd",
    "ds",
    "xd",
    "xs",
    "\u548c\u503c",
    "\u987a\u5b50",
    "\u8c79\u5b50",
)


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


@router.message(Command("start"), F.chat.type.in_(GROUP_TYPES))
async def start(message: Message, session_factory) -> None:
    await ensure_participant(message, session_factory)
    await message.reply(
        "\u673a\u5668\u4eba\u5df2\u8fd0\u884c\u3002\u5f00\u76d8\u540e\u53ef\u53d1\u9001\uff1a"
        "\u5927100\u3001dd100\u3001\u548c\u503c 10 100\u3001\u987a\u5b50100\u3001111 100\u3002"
    )


@router.message(Command("balance", "\u4f59\u989d"))
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
    await message.reply(f"\u5f53\u524d\u4f59\u989d\uff1a{wallet.balance if wallet else 0.00}")


def failure_message(user, *, item: str = "", reason: str) -> str:
    mention = f"<a href=tg://user?id={user.id}>{escape(user.full_name)}</a>"
    item_line = f"\u9519\u8bef\u9879\u76ee\uff1a{escape(item)}\n" if item else ""
    return (
        f"\u274c {mention}\uff08ID: {user.id}\uff09\u672c\u6761\u6295\u6ce8\u672a\u53d7\u7406\n"
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
        f"{index}. {escape(name)}（ID: {user_id}） · 流水 {turnover}"
        for index, (user_id, name, turnover) in enumerate(rows, 1)
    ]
    text = f"<b>🏆 {title}</b>\n\n" + ("\n".join(lines) if lines else "当前周期暂无有效投注。")
    await message.reply(text, parse_mode=ParseMode.HTML)


@router.message(F.chat.type.in_(GROUP_TYPES), F.dice)
async def collect_player_dice(message: Message, session_factory) -> None:
    user = message.from_user
    if user is None or message.dice is None or message.dice.emoji != "🎲":
        return
    completed = False
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
        deadline = datetime.fromisoformat(deadline_text) if deadline_text else datetime.now(UTC)
        if deadline <= datetime.now(UTC):
            return
        message_ids = list(snapshot.get("player_dice_message_ids", []))
        if message.message_id in message_ids or len(message_ids) >= 3:
            return
        values = list(snapshot.get("player_dice_values", []))
        message_ids.append(message.message_id)
        values.append(message.dice.value)
        round_.settings_snapshot = {
            **snapshot,
            "player_dice_values": values,
            "player_dice_message_ids": message_ids,
        }
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
            completed = True
    if completed:
        await message.reply("✅ 三颗骰子已收齐，本期进入结算。")


@router.message(F.chat.type.in_(GROUP_TYPES), F.text)
async def group_text(message: Message, session_factory) -> None:
    text = message.text or ""
    command = text.strip().lower()
    if command in {"签到", "qd"}:
        await process_checkin(message, session_factory)
        return
    ranking_period = {"日榜": "day", "周榜": "week", "月榜": "month"}.get(text.strip())
    if ranking_period:
        await send_group_ranking(message, session_factory, ranking_period)
        return
    try:
        items = parse_bets(text)
    except BetParseError as exc:
        user = message.from_user
        if user and any(token in text.lower() for token in BET_TOKENS):
            await message.reply(
                failure_message(user, item=exc.item, reason=exc.reason),
                parse_mode=ParseMode.HTML,
            )
        return
    user = message.from_user
    if user is None:
        return
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
    if not result.duplicate:
        await message.reply(
            f"\u2705 \u6295\u6ce8\u5df2\u53d7\u7406\uff0c\u5408\u8ba1 {result.total_amount}\uff0c"
            f"\u4f59\u989d {result.balance_after}"
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
