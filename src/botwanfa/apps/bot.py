import asyncio
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from botwanfa.config import get_settings
from botwanfa.db.models import Wallet
from botwanfa.db.session import create_engine_and_session
from botwanfa.domain.bets import BetParseError, parse_bets
from botwanfa.logging import configure_logging
from botwanfa.services.betting import BettingError, BettingService
from botwanfa.services.provisioning import provision_participant

router = Router()
betting = BettingService()
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


@router.message(Command("start"))
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


@router.message(F.chat.type.in_(GROUP_TYPES), F.text)
async def group_text(message: Message, session_factory) -> None:
    text = message.text or ""
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
    dispatcher.include_router(router)
    try:
        await dispatcher.start_polling(bot, session_factory=session_factory)
    finally:
        await engine.dispose()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())
