import asyncio
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from botwanfa.config import get_settings
from botwanfa.db.models import Round, TelegramGroup, Wallet
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


def is_super_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in get_settings().super_admin_ids)


def admin_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 运行状态", callback_data="admin:status")],
            [InlineKeyboardButton(text="🎮 群管理", callback_data="admin:groups")],
            [InlineKeyboardButton(text="📖 玩法说明", callback_data="admin:rules")],
            [InlineKeyboardButton(text="🛠 部署命令", callback_data="admin:ops")],
        ]
    )


def back_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ 返回主菜单", callback_data="admin:main")]]
    )


def admin_menu_text() -> str:
    return (
        "🛠 超级管理员菜单\n\n"
        "请选择要查看的功能。\n"
        "当前阶段已接入：运行状态、群列表、玩法说明、部署命令。"
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
    if message.chat.type == ChatType.PRIVATE:
        if is_super_admin(message.from_user.id if message.from_user else None):
            await message.reply(admin_menu_text(), reply_markup=admin_menu_markup())
        else:
            await message.reply(
                "你还不是超级管理员。\n\n"
                "请把你的 Telegram 数字ID 写入服务器 .env 的 SUPER_ADMIN_IDS 后重启服务。"
            )
        return

    await ensure_participant(message, session_factory)
    await message.reply(
        "\u673a\u5668\u4eba\u5df2\u8fd0\u884c\u3002\u5f00\u76d8\u540e\u53ef\u53d1\u9001\uff1a"
        "\u5927100\u3001dd100\u3001\u548c\u503c 10 100\u3001\u987a\u5b50100\u3001111 100\u3002"
    )


@router.message(Command("menu", "菜单"))
async def menu(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("请私聊机器人发送 /menu 打开超级管理员菜单。")
        return
    if not is_super_admin(message.from_user.id if message.from_user else None):
        await message.reply("你还不是超级管理员。")
        return
    await message.reply(admin_menu_text(), reply_markup=admin_menu_markup())


@router.callback_query(F.data.startswith("admin:"))
async def admin_callback(query: CallbackQuery, session_factory) -> None:
    if not is_super_admin(query.from_user.id if query.from_user else None):
        await query.answer("你还不是超级管理员", show_alert=True)
        return

    data = query.data or "admin:main"
    text = admin_menu_text()
    markup = admin_menu_markup()

    if data == "admin:status":
        async with session_factory() as session:
            group_count = await session.scalar(select(func.count(TelegramGroup.id)))
            active_round_count = await session.scalar(
                select(func.count(Round.id)).where(Round.status != "completed")
            )
        text = (
            "📊 运行状态\n\n"
            f"已记录群数量：{group_count or 0}\n"
            f"未完成期次数：{active_round_count or 0}\n"
            "服务组成：bot / scheduler / worker / sender / postgres / redis"
        )
        markup = back_menu_markup()
    elif data == "admin:groups":
        async with session_factory() as session:
            groups = (
                await session.execute(
                    select(TelegramGroup.id, TelegramGroup.title)
                    .order_by(TelegramGroup.created_at.desc())
                    .limit(20)
                )
            ).all()
        if groups:
            lines = [
                f"{idx}. {title or '未命名群'}（{group_id}）"
                for idx, (group_id, title) in enumerate(groups, 1)
            ]
            text = "🎮 群管理\n\n当前已记录群：\n" + "\n".join(lines)
        else:
            text = "🎮 群管理\n\n还没有记录任何群。请先把机器人拉进群，并在群里发送 /start。"
        markup = back_menu_markup()
    elif data == "admin:rules":
        text = (
            "📖 玩法说明\n\n"
            "支持：大、小、单、双、dd、ds、xd、xs、和值3-18、顺子、豹子、指定豹子。\n\n"
            "示例：\n"
            "大100\n"
            "dd100\n"
            "和值 10 100\n"
            "顺子100\n"
            "111 100"
        )
        markup = back_menu_markup()
    elif data == "admin:ops":
        text = (
            "🛠 部署命令\n\n"
            "查看状态：bash scripts/linux/status.sh\n"
            "更新代码：bash scripts/linux/update.sh\n"
            "备份数据：bash scripts/linux/backup.sh\n"
            "恢复数据：bash scripts/linux/restore.sh backups/xxx.bwf\n\n"
            "安装和更新完成后，脚本会主动给 SUPER_ADMIN_IDS 里的管理员发送通知。"
        )
        markup = back_menu_markup()
    elif data == "admin:main":
        text = admin_menu_text()
        markup = admin_menu_markup()

    if query.message:
        await query.message.edit_text(text, reply_markup=markup)
    await query.answer()


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
