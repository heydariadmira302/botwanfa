from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select
from sqlalchemy.engine import make_url

from botwanfa.backup_crypto import encrypt_file
from botwanfa.config import get_settings
from botwanfa.db.models import (
    AdminAction,
    BackupRecord,
    BetBatch,
    DeploymentControl,
    GameSettings,
    GroupMember,
    OddsSetting,
    OutboxMessage,
    Round,
    RoundPlayerSettlement,
    TelegramGroup,
    User,
    Wallet,
    WalletLedger,
    WinningStreak,
)
from botwanfa.presentation import (
    TREND_MAX_POINTS,
    TREND_MIN_POINTS,
    bet_label,
    money,
    player_mention,
    rules_text,
)
from botwanfa.services.drain import load_drain_progress, set_draining
from botwanfa.services.round_audit import RoundAuditReport, load_round_audit

router = Router(name="super_admin")
PAGE_SIZE = 8
ACTIVE_ROUND_STATUSES = (
    "waiting",
    "betting",
    "closed",
    "waiting_for_player_dice",
    "bot_rolling",
    "settling",
    "paused",
    "failed",
    "manual_review",
)
STATUS_LABELS = {
    "waiting": "等待开局",
    "betting": "下注中",
    "closed": "已封盘",
    "waiting_for_player_dice": "等待玩家掷骰",
    "bot_rolling": "机器人开奖",
    "settling": "结算中",
    "completed": "已完成",
    "paused": "已暂停",
    "failed": "运行异常",
    "manual_review": "等待人工处理",
}
ODDS_LABELS = {
    "big": "大",
    "small": "小",
    "odd": "单",
    "even": "双",
    "big_odd": "大单 dd",
    "big_even": "大双 ds",
    "small_odd": "小单 xd",
    "small_even": "小双 xs",
    "sum": "和值",
    "straight": "顺子",
    "any_triple": "任意豹子",
    "specific_triple": "指定豹子",
}
ODDS_CATEGORIES = {
    "basic": ("大小单双", ("big", "small", "odd", "even")),
    "combo": ("组合玩法", ("big_odd", "big_even", "small_odd", "small_even")),
    "sum": ("和值 3-18", ("sum",)),
    "special": ("顺子与豹子", ("straight", "any_triple", "specific_triple")),
}
ODDS_BATCH_CATEGORIES = {"basic", "combo", "sum"}
ODDS_TYPE_ORDER = {
    bet_type: index
    for index, bet_type in enumerate(
        bet_type
        for _, bet_types in ODDS_CATEGORIES.values()
        for bet_type in bet_types
    )
}
ACTION_LABELS = {
    "group_paused": "暂停群运行",
    "group_resumed": "恢复群运行",
    "setting_changed": "修改群参数",
    "odds_changed": "修改玩法倍率",
    "wallet_credit": "玩家上分",
    "wallet_debit": "玩家下分",
    "test_mode_enabled": "开启测试模式",
    "test_mode_disabled": "关闭测试模式",
    "rules_published": "发送玩法说明",
    "backup_created": "立即备份",
    "message_retried": "重试发送消息",
    "failed_message_deleted": "删除发送失败记录",
    "message_button_added": "添加消息按钮",
    "message_button_deleted": "删除消息按钮",
    "deployment_drain_enabled": "开始平滑更新准备",
    "deployment_drain_cancelled": "取消平滑更新准备",
}
MESSAGE_BUTTON_TEMPLATES = {
    "open": ("round_open", "开始下注"),
    "closed": ("round_closed", "停止下注"),
    "dice": ("player_dice_invite", "玩家掷骰邀请"),
    "result": ("round_result", "开奖结果与结算"),
    "rules": ("rules", "玩法说明"),
}
MESSAGE_TYPE_LABELS = {
    "text": "文字通知",
    "round_open": "开始下注",
    "round_closed": "停止下注",
    "player_dice_invite": "玩家掷骰邀请",
    "player_dice_ack": "玩家骰子识别回复",
    "dice_round": "机器人掷骰",
    "round_result": "开奖结果与结算",
    "trend_result": "历史开奖结果",
    "settlement_summary": "历史结算通知",
}


class AdminInput(StatesGroup):
    setting_value = State()
    message_button = State()
    player_search = State()
    round_search = State()
    wallet_adjustment = State()
    wallet_confirmation = State()


def is_super_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in get_settings().super_admin_ids)


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def admin_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("📊 运行总览", "a:o"), _button("🎮 群管理", "a:gl:0:manage")],
            [_button("🔎 查询玩家", "a:us:0"), _button("💳 玩家上下分", "a:gl:0:players")],
            [_button("🏆 群排行榜", "a:gl:0:ranking"), _button("📈 数据报表", "a:gl:0:report")],
            [_button("📨 发送队列", "a:mq:0"), _button("🧾 操作日志", "a:l:0")],
            [_button("🛠 平滑更新", "a:dr"), _button("💾 备份恢复", "a:bk")],
            [_button("🧪 测试模式", "a:gl:0:test")],
            [_button("🔍 期号查账", "a:rs"), _button("📖 玩法说明", "a:rules")],
        ]
    )


def _home_button() -> list[InlineKeyboardButton]:
    return [_button("🏠 管理首页", "a:h")]


def parse_public_round_code(text: str) -> str | None:
    match = re.search(r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", text)
    return match.group(0).lower() if match else None


def _signed_money(value: Decimal) -> str:
    return f"{'+' if value > 0 else ''}{money(value)}"


def round_audit_pages(report: RoundAuditReport) -> list[str]:
    returned = (
        money(report.total_returned)
        if report.total_returned is not None
        else "待结算"
    )
    net = (
        _signed_money(report.total_net)
        if report.total_net is not None
        else "待结算"
    )
    if report.dice:
        dice_text = (
            f"{report.dice[0]} - {report.dice[1]} - {report.dice[2]}"
            f"（和值 {sum(report.dice)}）"
        )
        dice_source = (
            "玩家掷骰"
            if report.dice_source and report.dice_source.startswith("player:")
            else "机器人掷骰"
        )
        dice_text = f"{dice_text} · {dice_source}"
    else:
        dice_text = "尚未开奖"
    header = (
        "<b>🔍 期号查账</b>\n\n"
        f"期号：<code>{report.public_code}</code>\n"
        f"群：{escape(report.group_title or str(report.group_id))}\n"
        f"状态：{STATUS_LABELS.get(report.status, report.status)}\n"
        f"骰子：{dice_text}\n"
        f"参与：{len(report.players)} 人　总投注：<b>{money(report.total_wagered)}</b>\n"
        f"总返还：<b>{returned}</b>　玩家净输赢：<b>{net}</b>"
    )
    if not report.players:
        return [f"{header}\n\n本期无人投注。"]

    player_blocks = []
    for index, player in enumerate(report.players, 1):
        lines = [
            f"{index}. {player_mention(player.user_id, player.display_name)}"
        ]
        for item in player.items:
            if item.won is None:
                result = "待结算"
            elif item.won:
                result = f"赢 · 返还 {money(item.payout)}"
            else:
                result = "输 · 返还 0.00"
            lines.append(
                f"• {bet_label(item.bet_type, item.bet_value)} "
                f"{money(item.amount)} × {money(item.odds)} · {result}"
            )
        player_returned = money(player.returned) if player.returned is not None else "待结算"
        player_net = _signed_money(player.net) if player.net is not None else "待结算"
        lines.append(
            f"合计：投注 <b>{money(player.wagered)}</b>　返还 <b>{player_returned}</b>　"
            f"净输赢 <b>{player_net}</b>"
        )
        if player.streak_reward > 0:
            lines.append(f"连胜奖励：<b>+{money(player.streak_reward)}</b>")
        if player.balance_after is not None:
            lines.append(f"结后余额：<b>{money(player.balance_after)}</b>")
        player_blocks.append("\n".join(lines))

    continuation = (
        "<b>🔍 期号查账（续）</b>\n"
        f"期号：<code>{report.public_code}</code>\n"
        f"群：{escape(report.group_title or str(report.group_id))}"
    )
    pages: list[str] = []
    current = header
    for block in player_blocks:
        for line in ("", *block.splitlines()):
            candidate = f"{current}\n{line}"
            if len(candidate) > 3800 and current != continuation:
                pages.append(current)
                current = continuation
                candidate = f"{current}\n\n{line}" if line else current
            current = candidate
    pages.append(current)
    return pages


async def _audit(session, admin_id: int, action: str, group_id: int | None, **details) -> None:
    session.add(
        AdminAction(
            admin_user_id=admin_id,
            group_id=group_id,
            action=action,
            details=details,
        )
    )


async def _home_view(session_factory) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        deployment = await session.get(DeploymentControl, 1)
        groups = int(await session.scalar(select(func.count(TelegramGroup.id))) or 0)
        running = int(
            await session.scalar(
                select(func.count(TelegramGroup.id)).where(
                    TelegramGroup.enabled.is_(True), TelegramGroup.paused.is_(False)
                )
            )
            or 0
        )
        paused = int(
            await session.scalar(
                select(func.count(TelegramGroup.id)).where(TelegramGroup.paused.is_(True))
            )
            or 0
        )
        active = int(
            await session.scalar(
                select(func.count(Round.id)).where(Round.status.in_(ACTIVE_ROUND_STATUSES))
            )
            or 0
        )
        failed_messages = int(
            await session.scalar(
                select(func.count(OutboxMessage.id)).where(OutboxMessage.status == "failed")
            )
            or 0
        )
    health = "🟢 正常" if failed_messages == 0 else f"🟠 有 {failed_messages} 条发送失败"
    deployment_status = "正在排空，等待当前期完成" if deployment and deployment.draining else "正常运行"
    text = (
        "<b>BOTWANFA 管理中心</b>\n\n"
        f"系统状态：{health}\n"
        f"更新状态：{deployment_status}\n"
        f"群组：{groups} 个  ·  运行：{running} 个  ·  暂停：{paused} 个\n"
        f"当前未完成期次：{active} 个\n\n"
        "请选择要执行的管理操作。"
    )
    return text, admin_menu_markup()


async def _drain_view(session_factory) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        deployment = await session.get(DeploymentControl, 1)
        progress = await load_drain_progress(
            session,
            failed_after_id=(
                deployment.outbox_start_id
                if deployment and deployment.draining
                else None
            ),
            drain_started_at=(
                deployment.requested_at
                if deployment and deployment.draining
                else None
            ),
        )
    draining = bool(deployment and deployment.draining)
    if not draining:
        status = "🟢 正常运行"
        summary = "各群会继续自动开始新期次。"
    elif progress.ready:
        status = "✅ 已排空，可以更新"
        summary = "所有群当前期和关键消息均已处理完成。"
    else:
        status = "🟡 正在并行收尾"
        summary = "已有期次继续运行，但所有群都不会再开始新一期。"
    requested_at = "-"
    if deployment and deployment.requested_at:
        requested_at = deployment.requested_at.astimezone(get_settings().tz).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    text = (
        "<b>🛠 平滑更新</b>\n\n"
        f"当前状态：{status}\n"
        f"开始时间：{requested_at}\n"
        f"{summary}\n\n"
        f"未完成期次：{progress.active_rounds}\n"
        f"├ 开放下注：{progress.betting_rounds}\n"
        f"├ 等待玩家掷骰：{progress.waiting_player_rounds}\n"
        f"├ 封盘/机器人掷骰：{progress.rolling_rounds}\n"
        f"├ 正在结算：{progress.settling_rounds}\n"
        f"└ 异常阻塞：{progress.blocked_rounds}\n"
        f"待发送关键消息：{progress.pending_round_messages}\n"
        f"发送失败关键消息：{progress.failed_round_messages}\n\n"
        "排空完成后，在服务器项目目录执行：\n"
        "<code>bash scripts/linux/update.sh</code>"
    )
    if draining:
        rows = [
            [_button("🔄 刷新进度", "a:dr")],
            [_button("取消更新准备", "a:dcx")],
            _home_button(),
        ]
    else:
        rows = [
            [_button("开始准备更新", "a:dx")],
            _home_button(),
        ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _overview_view(session_factory) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        player_count = int(await session.scalar(select(func.count(User.id))) or 0)
        wallet_total = await session.scalar(select(func.coalesce(func.sum(Wallet.balance), 0)))
        completed = int(
            await session.scalar(
                select(func.count(Round.id)).where(Round.status == "completed")
            )
            or 0
        )
        pending = int(
            await session.scalar(
                select(func.count(OutboxMessage.id)).where(
                    OutboxMessage.status.in_(("pending", "processing"))
                )
            )
            or 0
        )
        failed = int(
            await session.scalar(
                select(func.count(OutboxMessage.id)).where(OutboxMessage.status == "failed")
            )
            or 0
        )
        latest = await session.scalar(select(func.max(Round.created_at)))
    latest_text = latest.astimezone(get_settings().tz).strftime("%Y-%m-%d %H:%M:%S") if latest else "暂无"
    text = (
        "<b>📊 运行总览</b>\n\n"
        f"已记录玩家：{player_count}\n"
        f"全部钱包余额：{wallet_total or Decimal('0.00')}\n"
        f"已完成期次：{completed}\n"
        f"待发送消息：{pending}\n"
        f"发送失败消息：{failed}\n"
        f"最近开局时间：{latest_text}\n\n"
        "服务：bot / scheduler / worker / sender / PostgreSQL / Redis"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("📨 查看发送队列", "a:mq:0"), _button("🔄 刷新", "a:o")],
            _home_button(),
        ]
    )
    return text, markup


def parse_message_button_input(raw: str) -> tuple[str, str]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) >= 2:
        label, url = lines[0], lines[1]
    elif len(lines) == 1 and "|" in lines[0]:
        label, url = (part.strip() for part in lines[0].split("|", 1))
    elif len(lines) == 1:
        parts = lines[0].rsplit(maxsplit=1)
        if len(parts) != 2:
            raise ValueError("请分两行输入按钮文字和链接")
        label, url = parts
    else:
        raise ValueError("按钮文字和链接不能为空")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("链接必须是完整的 http:// 或 https:// 地址")
    if not 1 <= len(label) <= 32:
        raise ValueError("按钮文字长度应为1至32个字符")
    if len(url) > 2048:
        raise ValueError("链接过长")
    return label, url


async def _message_queue_view(
    session_factory, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    page = max(0, page)
    async with session_factory() as session:
        total = int(
            await session.scalar(
                select(func.count(OutboxMessage.id)).where(
                    OutboxMessage.status == "failed"
                )
            )
            or 0
        )
        rows = (
            await session.scalars(
                select(OutboxMessage)
                .where(OutboxMessage.status == "failed")
                .order_by(OutboxMessage.id.desc())
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()
    if total == 0:
        text = "<b>📨 发送队列</b>\n\n🟢 当前没有发送失败的消息。"
    else:
        lines = [
            "<b>📨 发送失败队列</b>",
            "",
            f"共 {total} 条，第 {page + 1} 页。可重试或删除失败记录。",
        ]
        for row in rows:
            message_type = MESSAGE_TYPE_LABELS.get(row.message_type, row.message_type)
            error = (row.last_error or "未记录错误").replace("\n", " ")[:140]
            lines.extend(
                (
                    "",
                    f"<b>#{row.id} · {escape(message_type)}</b>",
                    f"群：<code>{row.group_id}</code> · 已尝试 {row.attempt_count} 次",
                    f"原因：<code>{escape(error)}</code>",
                )
            )
        text = "\n".join(lines)
    buttons = []
    for row in rows:
        buttons.append(
            [
                _button(f"🔄 重试 #{row.id}", f"a:mr:{row.id}:{page}"),
                _button("🗑 删除", f"a:mqx:{row.id}:{page}"),
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_button("⬅️ 上一页", f"a:mq:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(_button("下一页 ➡️", f"a:mq:{page + 1}"))
    if nav:
        buttons.append(nav)
    if total:
        buttons.append(
            [
                _button("🔄 全部重试", "a:ma"),
                _button("🗑 批量删除", f"a:mqax:{page}"),
            ]
        )
    buttons.append([_button("刷新", f"a:mq:{page}"), _button("🏠 首页", "a:h")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _retry_failed_messages(
    session_factory, admin_id: int, message_id: int | None = None
) -> int:
    async with session_factory() as session, session.begin():
        query = (
            select(OutboxMessage)
            .where(OutboxMessage.status == "failed")
            .with_for_update()
        )
        if message_id is not None:
            query = query.where(OutboxMessage.id == message_id)
        rows = (await session.scalars(query)).all()
        for row in rows:
            row.status = "pending"
            row.available_at = datetime.now(UTC)
            row.last_error = None
            if row.message_type == "dice_round" and row.payload.get("round_id"):
                round_ = await session.get(
                    Round, int(row.payload["round_id"]), with_for_update=True
                )
                if round_ and round_.status == "manual_review":
                    round_.status = "bot_rolling"
        if rows:
            await _audit(
                session,
                admin_id,
                "message_retried",
                None,
                message_id=message_id,
                count=len(rows),
            )
    return len(rows)


async def _delete_failed_messages(
    session_factory, admin_id: int, message_id: int | None = None
) -> int:
    async with session_factory() as session, session.begin():
        query = (
            select(OutboxMessage)
            .where(OutboxMessage.status == "failed")
            .with_for_update()
        )
        if message_id is not None:
            query = query.where(OutboxMessage.id == message_id)
        rows = (await session.scalars(query)).all()
        for row in rows:
            await session.delete(row)
        if rows:
            await _audit(
                session,
                admin_id,
                "failed_message_deleted",
                None,
                message_id=message_id,
                count=len(rows),
            )
    return len(rows)


async def _message_buttons_view(
    session_factory, group_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        settings = await session.get(GameSettings, group_id)
    if group is None or settings is None:
        return await _group_view(session_factory, group_id)
    configured = settings.message_buttons or {}
    rows = []
    for code, (key, label) in MESSAGE_BUTTON_TEMPLATES.items():
        count = len(configured.get(key, []))
        rows.append([_button(f"{label} · {count}/8", f"a:mt:{group_id}:{code}")])
    rows.append([_button("⬅️ 返回群控制台", f"a:g:{group_id}")])
    text = (
        f"<b>🔗 {escape(group.title)} · 消息按钮</b>\n\n"
        "每类消息可配置最多 8 个跳转按钮；未配置的消息保持无按钮。"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _message_button_template_view(
    session_factory, group_id: int, code: str
) -> tuple[str, InlineKeyboardMarkup]:
    key, label = MESSAGE_BUTTON_TEMPLATES[code]
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        settings = await session.get(GameSettings, group_id)
    if group is None or settings is None:
        return await _group_view(session_factory, group_id)
    configured = list((settings.message_buttons or {}).get(key, []))
    lines = [f"<b>🔗 {escape(label)} · 消息按钮</b>", ""]
    if configured:
        for index, item in enumerate(configured, 1):
            lines.append(
                f"{index}. {escape(str(item.get('text', '')))}\n"
                f"   <code>{escape(str(item.get('url', ''))[:180])}</code>"
            )
    else:
        lines.append("当前没有按钮。")
    rows = [
        [
            _button(
                f"🗑 删除 {index + 1} · {str(item.get('text', ''))[:16]}",
                f"a:md:{group_id}:{code}:{index}",
            )
        ]
        for index, item in enumerate(configured)
    ]
    if len(configured) < 8:
        rows.append([_button("➕ 添加按钮", f"a:mb:{group_id}:{code}")])
    rows.append([_button("⬅️ 返回消息分类", f"a:mv:{group_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _group_target(mode: str, group_id: int) -> str:
    return {
        "players": f"a:pl:{group_id}:0",
        "ranking": f"a:r:{group_id}:day",
        "report": f"a:rp:{group_id}",
        "test": f"a:x:{group_id}",
    }.get(mode, f"a:g:{group_id}")


async def _groups_view(
    session_factory, page: int, mode: str
) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        total = int(await session.scalar(select(func.count(TelegramGroup.id))) or 0)
        groups = (
            await session.scalars(
                select(TelegramGroup)
                .order_by(TelegramGroup.created_at.desc())
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()
    title = {
        "players": "选择玩家所在群",
        "ranking": "选择要查看排行的群",
        "report": "选择要查看报表的群",
        "test": "选择要设置测试模式的群",
    }.get(mode, "选择要管理的群")
    rows: list[list[InlineKeyboardButton]] = []
    for group in groups:
        icon = "⏸" if group.paused else ("🟢" if group.enabled else "⚫")
        label = (group.title or f"群 {group.id}").strip()
        if len(label) > 28:
            label = label[:27] + "…"
        rows.append([_button(f"{icon} {label}", _group_target(mode, group.id))])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_button("⬅️ 上一页", f"a:gl:{page - 1}:{mode}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(_button("下一页 ➡️", f"a:gl:{page + 1}:{mode}"))
    if nav:
        rows.append(nav)
    rows.append([_button("🔄 刷新", f"a:gl:{page}:{mode}"), _button("🏠 首页", "a:h")])
    if not groups:
        text = f"<b>🎮 {title}</b>\n\n尚未记录群。把机器人加入群后，在群里发送 /start。"
    else:
        text = f"<b>🎮 {title}</b>\n\n共 {total} 个群，第 {page + 1} 页。"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _group_view(session_factory, group_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        settings = await session.get(GameSettings, group_id)
        active = await session.scalar(
            select(Round)
            .where(Round.group_id == group_id, Round.status.in_(ACTIVE_ROUND_STATUSES))
            .order_by(Round.id.desc())
            .limit(1)
        )
        members = int(
            await session.scalar(
                select(func.count(GroupMember.id)).where(GroupMember.group_id == group_id)
            )
            or 0
        )
        wallet_total = await session.scalar(
            select(func.coalesce(func.sum(Wallet.balance), 0)).where(Wallet.group_id == group_id)
        )
    if group is None or settings is None:
        return "群资料不存在。", InlineKeyboardMarkup(inline_keyboard=[_home_button()])
    state = "⏸ 已暂停" if group.paused else "🟢 自动运行"
    round_text = (
        f"第 {active.round_number} 期 · {STATUS_LABELS.get(active.status, active.status)}"
        if active
        else "等待下一期"
    )
    threshold = settings.player_dice_threshold
    if threshold is None:
        threshold_text = "关闭"
    elif threshold <= Decimal("0.01"):
        threshold_text = "默认最高下注者"
    else:
        threshold_text = str(threshold)
    test_text = "开启" if settings.test_mode else "关闭"
    text = (
        f"<b>🎮 {escape(group.title or f'群 {group.id}')}</b>\n"
        f"<code>{group.id}</code>\n\n"
        f"运行状态：{state}\n"
        f"当前期次：{round_text}\n"
        f"玩家数量：{members}  ·  钱包合计：{wallet_total or Decimal('0.00')}\n\n"
        f"下注/封盘开奖/下一局：{settings.betting_seconds}s / "
        f"{settings.rolling_seconds}s / {settings.next_round_seconds}s\n"
        f"最低下注：{settings.minimum_bet}  ·  玩家掷骰门槛：{threshold_text}\n"
        "走势期数："
        f"{min(max(settings.history_size, TREND_MIN_POINTS), TREND_MAX_POINTS)}"
        f"  ·  测试模式：{test_text}"
    )
    toggle = "▶️ 恢复运行" if group.paused else "⏸ 暂停运行"
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(toggle, f"a:p:{group_id}"), _button("🔄 刷新", f"a:g:{group_id}")],
            [_button("⏱ 时间设置", f"a:t:{group_id}"), _button("💰 底注与掷骰", f"a:b:{group_id}")],
            [_button("🎯 倍率设置", f"a:oc:{group_id}"), _button("🎁 签到与连胜", f"a:w:{group_id}")],
            [_button("👤 玩家管理", f"a:pl:{group_id}:0"), _button("🏆 排行榜", f"a:r:{group_id}:day")],
            [_button("📈 走势与报表", f"a:d:{group_id}"), _button("🧪 测试模式", f"a:x:{group_id}")],
            [_button("🔗 消息按钮", f"a:mv:{group_id}"), _button("📣 发送玩法说明", f"a:pub:{group_id}")],
            [_button("⬅️ 群列表", "a:gl:0:manage"), _button("🏠 首页", "a:h")],
        ]
    )
    return text, markup


async def _time_view(session_factory, group_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        settings = await session.get(GameSettings, group_id)
    if group is None or settings is None:
        return await _group_view(session_factory, group_id)
    rows = []
    fields = (
        ("下注窗口", "bs", settings.betting_seconds),
        ("封盘到开奖", "rs", settings.rolling_seconds),
        ("结算到下一局", "ns", settings.next_round_seconds),
        ("玩家掷骰窗口", "ps", settings.player_dice_seconds),
    )
    for label, code, value in fields:
        rows.append(
            [
                _button("−5", f"a:ta:{group_id}:{code}:-5"),
                _button(f"{label} {value}s", f"a:i:{group_id}:{code}"),
                _button("+5", f"a:ta:{group_id}:{code}:5"),
            ]
        )
    rows.append([_button("⬅️ 返回群控制台", f"a:g:{group_id}")])
    text = (
        f"<b>⏱ {escape(group.title)} · 时间设置</b>\n\n"
        "点击中间数值可直接输入，修改从下一期生效。"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _basic_view(session_factory, group_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        settings = await session.get(GameSettings, group_id)
    if group is None or settings is None:
        return await _group_view(session_factory, group_id)
    if settings.player_dice_threshold is None:
        threshold = "关闭"
    elif settings.player_dice_threshold <= Decimal("0.01"):
        threshold = "默认开启（本期投注最高者）"
    else:
        threshold = settings.player_dice_threshold
    text = (
        f"<b>💰 {escape(group.title)} · 底注与玩家掷骰</b>\n\n"
        f"最低下注：<b>{settings.minimum_bet}</b>\n"
        f"玩家掷骰门槛：<b>{threshold}</b>\n\n"
        "默认由本期累计有效投注最高者掷骰；金额相同则先达到者优先。"
        "无人下注时机器人直接掷骰，不等待玩家。"
    )
    rows = [
        [_button("✏️ 修改最低下注", f"a:i:{group_id}:min")],
        [
            _button(
                "✏️ 设置掷骰门槛" if settings.player_dice_threshold is None else "✏️ 修改掷骰门槛",
                f"a:i:{group_id}:dice",
            )
        ],
    ]
    if settings.player_dice_threshold is not None:
        rows.append([_button("🚫 关闭玩家掷骰", f"a:pd:{group_id}:off")])
    rows.append([_button("⬅️ 返回群控制台", f"a:g:{group_id}")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, markup


def _odds_name(row: OddsSetting) -> str:
    base = ODDS_LABELS.get(row.bet_type, row.bet_type)
    if row.bet_value:
        base = f"{base} {row.bet_value}"
    return base


def _odds_category_for(row: OddsSetting) -> str:
    for category, (_, bet_types) in ODDS_CATEGORIES.items():
        if row.bet_type in bet_types:
            return category
    return "basic"


def _odds_sort_key(row: OddsSetting) -> tuple[int, int | str]:
    value: int | str = int(row.bet_value) if row.bet_value.isdigit() else row.bet_value
    return ODDS_TYPE_ORDER.get(row.bet_type, len(ODDS_TYPE_ORDER)), value


def _odds_menu_markup(group_id: int, odds: list[OddsSetting]) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    for category, (label, bet_types) in ODDS_CATEGORIES.items():
        items = [item for item in odds if item.bet_type in bet_types]
        enabled = sum(item.enabled for item in items)
        buttons.append(
            _button(f"{label} · {enabled}/{len(items)}", f"a:oc:{group_id}:{category}")
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:2],
            buttons[2:],
            [_button("⬅️ 返回群控制台", f"a:g:{group_id}")],
        ]
    )


def _odds_items_markup(
    group_id: int, odds: list[OddsSetting], category: str
) -> InlineKeyboardMarkup:
    columns = 3 if category == "sum" else 2
    item_buttons = [
        _button(
            f"{'✅' if item.enabled else '⛔'} {_odds_name(item)} ×{item.payout_multiplier}",
            f"a:oi:{group_id}:{item.id}:{category}",
        )
        for item in sorted(odds, key=_odds_sort_key)
    ]
    rows = [item_buttons[index : index + columns] for index in range(0, len(item_buttons), columns)]
    if category in ODDS_BATCH_CATEGORIES:
        rows.append([_button("⚙️ 统一设置本类倍率", f"a:ob:{group_id}:{category}")])
    rows.append(
        [
            _button("⬅️ 返回倍率分类", f"a:oc:{group_id}"),
            _button("🏠 群控制台", f"a:g:{group_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _odds_view(
    session_factory, group_id: int, category: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        odds = (
            await session.scalars(
                select(OddsSetting)
                .where(OddsSetting.group_id == group_id)
            )
        ).all()
    if group is None:
        return "群资料不存在。", InlineKeyboardMarkup(inline_keyboard=[_home_button()])
    total = len(odds)
    if category not in ODDS_CATEGORIES:
        text = (
            f"<b>🎯 {escape(group.title)} · 倍率设置</b>\n\n"
            f"共 {total} 个赔率项，已按玩法分成 4 类。进入分类即可一次查看全部项目，"
            "无需逐页翻找。\n\n✅ 表示启用，⛔ 表示停用；返还倍数包含本金。"
        )
        return text, _odds_menu_markup(group_id, odds)

    category_label, bet_types = ODDS_CATEGORIES[category]
    category_odds = [item for item in odds if item.bet_type in bet_types]
    enabled = sum(item.enabled for item in category_odds)
    batch_hint = (
        "也可统一设置本类全部倍率。"
        if category in ODDS_BATCH_CATEGORIES
        else "不同特殊玩法的倍率差异较大，请逐项修改。"
    )
    text = (
        f"<b>🎯 {escape(group.title)} · {category_label}</b>\n\n"
        f"已启用 {enabled}/{len(category_odds)} 项。点击单项修改倍率或启停；"
        f"{batch_hint}"
    )
    return text, _odds_items_markup(group_id, category_odds, category)


async def _welfare_view(session_factory, group_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        settings = await session.get(GameSettings, group_id)
    if group is None or settings is None:
        return await _group_view(session_factory, group_id)
    rewards = "、".join(f"{count}连胜={reward}" for count, reward in settings.streak_rewards.items())
    text = (
        f"<b>🎁 {escape(group.title)} · 签到与连胜</b>\n\n"
        f"签到范围：{settings.checkin_min} 至 {settings.checkin_max}\n"
        f"签到步进：{settings.checkin_step}\n"
        f"连胜奖励：{'开启' if settings.streak_enabled else '关闭'}\n"
        f"奖励档位：{escape(rewards or '未设置')}"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("签到最小值", f"a:i:{group_id}:cmin"), _button("签到最大值", f"a:i:{group_id}:cmax")],
            [_button("签到步进", f"a:i:{group_id}:cstep"), _button("连胜档位", f"a:i:{group_id}:streak")],
            [_button("关闭连胜" if settings.streak_enabled else "开启连胜", f"a:st:{group_id}")],
            [_button("⬅️ 返回群控制台", f"a:g:{group_id}")],
        ]
    )
    return text, markup


async def _data_view(session_factory, group_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        settings = await session.get(GameSettings, group_id)
    if group is None or settings is None:
        return await _group_view(session_factory, group_id)
    visible_history = min(max(settings.history_size, TREND_MIN_POINTS), TREND_MAX_POINTS)
    legacy_note = (
        f"\n旧配置为 {settings.history_size} 期，当前自动按 {visible_history} 期展示。"
        if settings.history_size != visible_history
        else ""
    )
    text = (
        f"<b>📈 {escape(group.title)} · 走势与数据</b>\n\n"
        f"走势图横屏滚动显示最近 <b>{visible_history}</b> 期，每行14期且不显示格内期号。"
        f"{legacy_note}\n报表按当前群独立统计。"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("28期", f"a:hs:{group_id}:28"), _button("56期", f"a:hs:{group_id}:56"), _button("84期", f"a:hs:{group_id}:84")],
            [_button("✏️ 自定义走势期数", f"a:i:{group_id}:history")],
            [_button("📊 查看群报表", f"a:rp:{group_id}"), _button("🏆 查看排行榜", f"a:r:{group_id}:day")],
            [_button("⬅️ 返回群控制台", f"a:g:{group_id}")],
        ]
    )
    return text, markup


async def _test_view(session_factory, group_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        settings = await session.get(GameSettings, group_id)
    if group is None or settings is None:
        return await _group_view(session_factory, group_id)
    state = "🟠 已开启" if settings.test_mode else "⚪ 已关闭"
    text = (
        f"<b>🧪 {escape(group.title)} · 测试模式</b>\n\n"
        f"当前状态：{state}\n\n"
        "开启后，新一期会使用更短的测试等待时间；该设置仅影响当前群。"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("关闭测试模式" if settings.test_mode else "开启测试模式", f"a:xt:{group_id}")],
            [_button("⬅️ 返回群控制台", f"a:g:{group_id}")],
        ]
    )
    return text, markup


async def _players_view(
    session_factory, group_id: int, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        total = int(
            await session.scalar(select(func.count(Wallet.id)).where(Wallet.group_id == group_id))
            or 0
        )
        players = (
            await session.execute(
                select(User, Wallet)
                .join(Wallet, Wallet.user_id == User.id)
                .where(Wallet.group_id == group_id)
                .order_by(Wallet.updated_at.desc())
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()
    rows = [
        [_button(f"{user.display_name[:18]} · {wallet.balance}", f"a:u:{group_id}:{user.id}:all")]
        for user, wallet in players
    ]
    rows.insert(0, [_button("🔎 搜索玩家", f"a:us:{group_id}")])
    nav = []
    if page > 0:
        nav.append(_button("⬅️ 上一页", f"a:pl:{group_id}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(_button("下一页 ➡️", f"a:pl:{group_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_button("⬅️ 返回群控制台", f"a:g:{group_id}")])
    text = (
        f"<b>👤 {escape(group.title if group else str(group_id))} · 玩家管理</b>\n\n"
        f"共 {total} 个钱包。可按用户名、@用户名、用户ID或转发消息搜索。"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _player_groups_view(
    session_factory, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        wallets = (
            await session.execute(
                select(TelegramGroup, Wallet)
                .join(Wallet, Wallet.group_id == TelegramGroup.id)
                .where(Wallet.user_id == user_id)
                .order_by(TelegramGroup.created_at.desc())
            )
        ).all()
    if user is None or not wallets:
        return "玩家钱包不存在。", InlineKeyboardMarkup(inline_keyboard=[_home_button()])
    if len(wallets) == 1:
        return await _player_view(session_factory, wallets[0][0].id, user_id, "all")
    rows = [
        [
            _button(
                f"{(group.title or str(group.id))[:24]} · 余额 {wallet.balance}",
                f"a:u:{group.id}:{user_id}:all",
            )
        ]
        for group, wallet in wallets
    ]
    rows.append(_home_button())
    text = (
        f"<b>🔎 {escape(user.display_name)}</b>\n\n"
        "该玩家在多个群有独立钱包，请选择目标群。"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _period_start(period: str) -> datetime | None:
    tz = get_settings().tz
    now = datetime.now(tz)
    if period == "day":
        local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        local = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif period == "month":
        local = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return local.astimezone(UTC)


async def _player_view(
    session_factory, group_id: int, user_id: int, period: str
) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        user = await session.get(User, user_id)
        wallet = await session.scalar(
            select(Wallet).where(Wallet.group_id == group_id, Wallet.user_id == user_id)
        )
        streak = await session.scalar(
            select(WinningStreak).where(
                WinningStreak.group_id == group_id, WinningStreak.user_id == user_id
            )
        )
        query = select(RoundPlayerSettlement).where(
            RoundPlayerSettlement.group_id == group_id,
            RoundPlayerSettlement.user_id == user_id,
        )
        if period in {"r10", "r30", "r50"}:
            count = int(period[1:])
            settlements = (
                await session.scalars(
                    query.order_by(RoundPlayerSettlement.settled_at.desc()).limit(count)
                )
            ).all()
        else:
            start = _period_start(period)
            if start is not None:
                query = query.where(RoundPlayerSettlement.settled_at >= start)
            settlements = (await session.scalars(query)).all()
    if group is None or user is None or wallet is None:
        return "玩家资料不存在。", InlineKeyboardMarkup(inline_keyboard=[_home_button()])
    wagered = sum((item.wagered for item in settlements), Decimal("0.00"))
    returned = sum((item.returned for item in settlements), Decimal("0.00"))
    net = sum((item.net for item in settlements), Decimal("0.00"))
    wins = sum(item.net > 0 for item in settlements)
    losses = sum(item.net < 0 for item in settlements)
    draws = len(settlements) - wins - losses
    latest = max((item.settled_at for item in settlements), default=None)
    latest_text = latest.astimezone(get_settings().tz).strftime("%Y-%m-%d %H:%M") if latest else "暂无"
    period_name = {
        "day": "今日",
        "week": "本周",
        "month": "本月",
        "all": "全部",
        "r10": "最近10局",
        "r30": "最近30局",
        "r50": "最近50局",
    }.get(period, "全部")
    username = f"@{user.username}" if user.username else "未设置用户名"
    text = (
        f"<b>👤 {escape(user.display_name)}</b>\n"
        f"{escape(username)} · <code>{user.id}</code>\n"
        f"群：{escape(group.title)}\n\n"
        f"统计周期：<b>{period_name}</b>\n"
        f"当前余额：<b>{wallet.balance}</b>\n"
        f"投注流水：{wagered}\n"
        f"中奖返还：{returned}\n"
        f"净输赢：{net}\n"
        f"参与局数：{len(settlements)}  ·  盈 {wins} / 亏 {losses} / 平 {draws}\n"
        f"当前连胜：{streak.current_count if streak else 0}  ·  最高连胜：{streak.highest_count if streak else 0}\n"
        f"最近参与：{latest_text}"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("今日", f"a:u:{group_id}:{user_id}:day"), _button("本周", f"a:u:{group_id}:{user_id}:week"), _button("本月", f"a:u:{group_id}:{user_id}:month")],
            [_button("全部", f"a:u:{group_id}:{user_id}:all"), _button("近10局", f"a:u:{group_id}:{user_id}:r10"), _button("近30局", f"a:u:{group_id}:{user_id}:r30"), _button("近50局", f"a:u:{group_id}:{user_id}:r50")],
            [_button("➕ 上分", f"a:ua:{group_id}:{user_id}:credit"), _button("➖ 下分", f"a:ua:{group_id}:{user_id}:debit")],
            [_button("⬅️ 玩家列表", f"a:pl:{group_id}:0"), _button("🏠 首页", "a:h")],
        ]
    )
    return text, markup


async def _ranking_view(
    session_factory, group_id: int, period: str
) -> tuple[str, InlineKeyboardMarkup]:
    start = _period_start(period)
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        query = (
            select(User.id, User.display_name, func.sum(BetBatch.total_amount).label("turnover"))
            .join(User, User.id == BetBatch.user_id)
            .where(BetBatch.group_id == group_id)
            .group_by(User.id, User.display_name)
            .order_by(func.sum(BetBatch.total_amount).desc())
            .limit(5)
        )
        if start is not None:
            query = query.where(BetBatch.created_at >= start)
        rows = (await session.execute(query)).all()
    name = {"day": "日榜", "week": "周榜", "month": "月榜"}.get(period, "排行榜")
    lines = [
        f"{index}. {escape(display_name)}（<code>{user_id}</code>） · {turnover}"
        for index, (user_id, display_name, turnover) in enumerate(rows, 1)
    ]
    text = (
        f"<b>🏆 {escape(group.title if group else str(group_id))} · {name}</b>\n\n"
        + ("\n".join(lines) if lines else "当前周期暂无有效投注。")
        + "\n\n按有效投注流水排序，显示前5名。"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("日榜", f"a:r:{group_id}:day"), _button("周榜", f"a:r:{group_id}:week"), _button("月榜", f"a:r:{group_id}:month")],
            [_button("⬅️ 返回群控制台", f"a:g:{group_id}")],
        ]
    )
    return text, markup


async def _report_view(session_factory, group_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        group = await session.get(TelegramGroup, group_id)
        rounds = int(
            await session.scalar(select(func.count(Round.id)).where(Round.group_id == group_id))
            or 0
        )
        completed = int(
            await session.scalar(
                select(func.count(Round.id)).where(
                    Round.group_id == group_id, Round.status == "completed"
                )
            )
            or 0
        )
        wagered = await session.scalar(
            select(func.coalesce(func.sum(BetBatch.total_amount), 0)).where(
                BetBatch.group_id == group_id
            )
        )
        returned = await session.scalar(
            select(func.coalesce(func.sum(RoundPlayerSettlement.returned), 0)).where(
                RoundPlayerSettlement.group_id == group_id
            )
        )
        players = int(
            await session.scalar(select(func.count(Wallet.id)).where(Wallet.group_id == group_id))
            or 0
        )
        balance = await session.scalar(
            select(func.coalesce(func.sum(Wallet.balance), 0)).where(Wallet.group_id == group_id)
        )
    wagered = Decimal(wagered or 0)
    returned = Decimal(returned or 0)
    text = (
        f"<b>📈 {escape(group.title if group else str(group_id))} · 数据报表</b>\n\n"
        f"全部期次：{rounds}\n"
        f"已完成期次：{completed}\n"
        f"玩家钱包：{players}\n"
        f"累计投注流水：{wagered}\n"
        f"累计中奖返还：{returned}\n"
        f"玩家累计净输赢：{returned - wagered}\n"
        f"当前钱包总余额：{balance or Decimal('0.00')}"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[_button("🔄 刷新", f"a:rp:{group_id}")], [_button("⬅️ 返回群控制台", f"a:g:{group_id}")]]
    )
    return text, markup


async def _logs_view(session_factory, page: int) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        total = int(await session.scalar(select(func.count(AdminAction.id))) or 0)
        actions = (
            await session.scalars(
                select(AdminAction)
                .order_by(AdminAction.created_at.desc())
                .offset(page * 10)
                .limit(10)
            )
        ).all()
    lines = []
    for item in actions:
        at = item.created_at.astimezone(get_settings().tz).strftime("%m-%d %H:%M")
        group = f"群 {item.group_id}" if item.group_id else "系统"
        lines.append(
            f"{at} · {escape(ACTION_LABELS.get(item.action, item.action))} · {group} · 管理员 {item.admin_user_id}"
        )
    rows: list[list[InlineKeyboardButton]] = []
    nav = []
    if page > 0:
        nav.append(_button("⬅️ 上一页", f"a:l:{page - 1}"))
    if (page + 1) * 10 < total:
        nav.append(_button("下一页 ➡️", f"a:l:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(_home_button())
    text = "<b>🧾 管理员操作日志</b>\n\n" + ("\n".join(lines) if lines else "暂无操作记录。")
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _backup_files() -> list[Path]:
    backup_dir = Path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    return sorted(backup_dir.glob("*.bwf"), key=lambda item: item.stat().st_mtime, reverse=True)


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _backup_view() -> tuple[str, InlineKeyboardMarkup]:
    files = _backup_files()
    latest = files[0].name if files else "暂无"
    text = (
        "<b>💾 备份与恢复</b>\n\n"
        f"加密备份数量：{len(files)}\n"
        f"最近备份：{escape(latest)}\n\n"
        "立即备份会生成 PostgreSQL 一致性快照，并使用部署时设置的密钥加密。"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("📦 立即备份", "a:bn"), _button("📂 备份列表", "a:bl")],
            [_button("♻️ 恢复备份", "a:br")],
            _home_button(),
        ]
    )
    return text, markup


async def _backup_list_view() -> tuple[str, InlineKeyboardMarkup]:
    files = _backup_files()[:20]
    lines = []
    for index, item in enumerate(files, 1):
        size = item.stat().st_size / 1024 / 1024
        modified = datetime.fromtimestamp(item.stat().st_mtime, get_settings().tz).strftime(
            "%Y-%m-%d %H:%M"
        )
        lines.append(f"{index}. <code>{escape(item.name)}</code> · {size:.2f} MB · {modified}")
    text = "<b>📂 加密备份列表</b>\n\n" + ("\n".join(lines) if lines else "暂无备份文件。")
    markup = InlineKeyboardMarkup(inline_keyboard=[[_button("⬅️ 返回备份", "a:bk")]])
    return text, markup


async def _create_backup(session_factory, admin_id: int) -> str:
    settings = get_settings()
    passphrase = settings.backup_passphrase.get_secret_value()
    if len(passphrase) < 12:
        raise ValueError("备份加密密钥未正确配置")
    url = make_url(settings.database_url)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = Path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    raw = backup_dir / f".admin-{stamp}-{uuid.uuid4().hex}.dump"
    target = backup_dir / f"botwanfa-{stamp}.bwf"
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    process = await asyncio.create_subprocess_exec(
        "pg_dump",
        "--host",
        url.host or "localhost",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "botwanfa",
        "--dbname",
        url.database or "botwanfa",
        "--format=custom",
        "--file",
        str(raw),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raw.unlink(missing_ok=True)
        raise RuntimeError(stderr.decode("utf-8", errors="replace")[-1000:])
    try:
        await asyncio.to_thread(encrypt_file, raw, target, passphrase)
    finally:
        raw.unlink(missing_ok=True)
    checksum = await asyncio.to_thread(_file_checksum, target)
    async with session_factory() as session, session.begin():
        session.add(
            BackupRecord(
                filename=target.name,
                backup_type="manual",
                status="completed",
                size_bytes=target.stat().st_size,
                checksum=checksum,
            )
        )
        await _audit(session, admin_id, "backup_created", None, filename=target.name)
    return target.name


async def _show(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    if query.message:
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await query.message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)


@router.message(Command("start", "menu", "菜单"), F.chat.type == ChatType.PRIVATE)
async def admin_start(message: Message, session_factory, state: FSMContext) -> None:
    await state.clear()
    if not is_super_admin(message.from_user.id if message.from_user else None):
        await message.answer(
            "当前账号不在超级管理员名单中。请检查服务器 .env 的 SUPER_ADMIN_IDS。"
        )
        return
    text, markup = await _home_view(session_factory)
    await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("a:"))
async def admin_callback(
    query: CallbackQuery, session_factory, state: FSMContext, bot: Bot
) -> None:
    admin_id = query.from_user.id if query.from_user else 0
    if not is_super_admin(admin_id):
        await query.answer("当前账号没有超级管理员权限", show_alert=True)
        return
    data = query.data or "a:h"
    parts = data.split(":")
    action = parts[1]
    await query.answer()
    if action not in {"uc", "ux"} and await state.get_state() is not None:
        await state.clear()

    if action == "h":
        await _show(query, *(await _home_view(session_factory)))
    elif action == "o":
        await _show(query, *(await _overview_view(session_factory)))
    elif action == "rs":
        await state.set_state(AdminInput.round_search)
        await query.message.answer(
            "请发送群消息中显示的 32 位期号。\n\n"
            "可以直接粘贴整条开奖消息，机器人会自动提取期号。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_button("取消并返回", "a:h")]]
            ),
        )
    elif action == "dr":
        await _show(query, *(await _drain_view(session_factory)))
    elif action == "dx":
        await _show(
            query,
            "<b>确认开始准备更新？</b>\n\n"
            "确认后所有群会停止创建新期次；正在进行的期次继续下注、掷骰、开奖和结算，互不等待。",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("确认开始", "a:de"), _button("取消", "a:dr")]
                ]
            ),
        )
    elif action == "de":
        async with session_factory() as session, session.begin():
            deployment = await set_draining(session, True, requested_by=admin_id)
            await _audit(
                session,
                admin_id,
                "deployment_drain_enabled",
                None,
                generation=deployment.generation,
            )
        await query.message.answer("已开始排空；各群当前期会并行完成，不会再开新一期。")
        await _show(query, *(await _drain_view(session_factory)))
    elif action == "dcx":
        await _show(
            query,
            "<b>确认取消更新准备？</b>\n\n"
            "取消后，已完成当前期的群会重新自动开始下一期。",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("确认取消", "a:dc"), _button("继续等待", "a:dr")]
                ]
            ),
        )
    elif action == "dc":
        async with session_factory() as session, session.begin():
            await set_draining(session, False, requested_by=admin_id)
            await _audit(session, admin_id, "deployment_drain_cancelled", None)
        await query.message.answer("已取消更新准备，各群将恢复自动开新一期。")
        await _show(query, *(await _drain_view(session_factory)))
    elif action == "mq":
        await _show(query, *(await _message_queue_view(session_factory, int(parts[2]))))
    elif action == "mr":
        count = await _retry_failed_messages(
            session_factory, admin_id, int(parts[2])
        )
        await query.message.answer("已重新加入发送队列。" if count else "该消息已处理。")
        await _show(
            query,
            *(await _message_queue_view(session_factory, int(parts[3]))),
        )
    elif action == "ma":
        count = await _retry_failed_messages(session_factory, admin_id)
        await query.message.answer(f"已将 {count} 条消息重新加入发送队列。")
        await _show(query, *(await _message_queue_view(session_factory, 0)))
    elif action == "mqx":
        message_id, page = int(parts[2]), int(parts[3])
        await _show(
            query,
            f"<b>确认删除失败记录 #{message_id}？</b>\n\n"
            "删除后该记录不会再次发送，操作会写入管理员日志。",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _button("确认删除", f"a:mqd:{message_id}:{page}"),
                        _button("取消", f"a:mq:{page}"),
                    ]
                ]
            ),
        )
    elif action == "mqd":
        message_id, page = int(parts[2]), int(parts[3])
        deleted = await _delete_failed_messages(session_factory, admin_id, message_id)
        if deleted:
            await query.message.answer("失败记录已删除。")
        else:
            await query.message.answer("该记录已经处理。")
        await _show(query, *(await _message_queue_view(session_factory, page)))
    elif action == "mqax":
        page = int(parts[2])
        await _show(
            query,
            "<b>确认批量删除发送失败记录？</b>\n\n"
            "确认后会删除当前全部发送失败记录，且不再重新发送。",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _button("确认批量删除", f"a:mqad:{page}"),
                        _button("取消", f"a:mq:{page}"),
                    ]
                ]
            ),
        )
    elif action == "mqad":
        page = int(parts[2])
        deleted = await _delete_failed_messages(session_factory, admin_id)
        await query.message.answer(f"已删除 {deleted} 条失败记录。")
        await _show(query, *(await _message_queue_view(session_factory, page)))
    elif action == "gl":
        await _show(query, *(await _groups_view(session_factory, int(parts[2]), parts[3])))
    elif action == "g":
        await _show(query, *(await _group_view(session_factory, int(parts[2]))))
    elif action == "mv":
        await _show(query, *(await _message_buttons_view(session_factory, int(parts[2]))))
    elif action == "mt":
        await _show(
            query,
            *(await _message_button_template_view(
                session_factory, int(parts[2]), parts[3]
            )),
        )
    elif action == "mb":
        group_id, code = int(parts[2]), parts[3]
        await state.set_state(AdminInput.message_button)
        await state.set_data({"group_id": group_id, "template_code": code})
        await query.message.answer(
            "请分两行发送按钮资料：\n\n"
            "<code>充值提现\nhttps://example.com/recharge</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("取消并返回", f"a:mt:{group_id}:{code}")]
                ]
            ),
        )
    elif action == "md":
        group_id, code, index = int(parts[2]), parts[3], int(parts[4])
        key, _ = MESSAGE_BUTTON_TEMPLATES[code]
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if settings:
                configured = dict(settings.message_buttons or {})
                buttons = list(configured.get(key, []))
                if 0 <= index < len(buttons):
                    removed = buttons.pop(index)
                    configured[key] = buttons
                    settings.message_buttons = configured
                    settings.version += 1
                    await _audit(
                        session,
                        admin_id,
                        "message_button_deleted",
                        group_id,
                        template=key,
                        text=removed.get("text"),
                    )
        await _show(
            query,
            *(await _message_button_template_view(session_factory, group_id, code)),
        )
    elif action == "p":
        group_id = int(parts[2])
        async with session_factory() as session, session.begin():
            group = await session.get(TelegramGroup, group_id, with_for_update=True)
            if group:
                group.paused = not group.paused
                audit_action = "group_paused" if group.paused else "group_resumed"
                await _audit(session, admin_id, audit_action, group_id)
        await _show(query, *(await _group_view(session_factory, group_id)))
    elif action == "t":
        await _show(query, *(await _time_view(session_factory, int(parts[2]))))
    elif action == "ta":
        group_id, code, delta = int(parts[2]), parts[3], int(parts[4])
        fields = {"bs": "betting_seconds", "rs": "rolling_seconds", "ns": "next_round_seconds", "ps": "player_dice_seconds"}
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if settings:
                field = fields[code]
                value = max(1, min(3600, int(getattr(settings, field)) + delta))
                setattr(settings, field, value)
                settings.version += 1
                await _audit(session, admin_id, "setting_changed", group_id, field=field, value=value)
        await _show(query, *(await _time_view(session_factory, group_id)))
    elif action == "b":
        await _show(query, *(await _basic_view(session_factory, int(parts[2]))))
    elif action == "pd":
        group_id = int(parts[2])
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if settings:
                settings.player_dice_threshold = None
                settings.version += 1
                await _audit(session, admin_id, "setting_changed", group_id, field="player_dice_threshold", value=None)
        await _show(query, *(await _basic_view(session_factory, group_id)))
    elif action == "ol":
        # Compatibility for buttons sent by versions that used numbered odds pages.
        await _show(query, *(await _odds_view(session_factory, int(parts[2]))))
    elif action == "oc":
        category = parts[3] if len(parts) > 3 else None
        await _show(query, *(await _odds_view(session_factory, int(parts[2]), category)))
    elif action == "oi":
        group_id, odds_id = int(parts[2]), int(parts[3])
        async with session_factory() as session:
            odds = await session.get(OddsSetting, odds_id)
        if odds is None or odds.group_id != group_id:
            await query.message.answer("赔率项目不存在。")
            return
        category = parts[4] if len(parts) > 4 else _odds_category_for(odds)
        return_to = f"a:oc:{group_id}:{category}"
        await state.set_state(AdminInput.setting_value)
        await state.set_data(
            {
                "kind": "odds",
                "group_id": group_id,
                "odds_id": odds_id,
                "category": category,
                "return_to": return_to,
            }
        )
        await query.message.answer(
            f"请输入 <b>{escape(_odds_name(odds))}</b> 的返还倍数。\n"
            f"当前值：{odds.payout_multiplier} · 当前状态：{'启用' if odds.enabled else '停用'}\n"
            "示例：<code>2.00</code>\n\n修改倍率不会改变启停状态。",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _button(
                            "⛔ 停用此玩法" if odds.enabled else "✅ 启用此玩法",
                            f"a:oe:{group_id}:{odds_id}:{category}",
                        )
                    ],
                    [_button("取消并返回", return_to)],
                ]
            ),
        )
    elif action == "ob":
        group_id, category = int(parts[2]), parts[3]
        if category not in ODDS_BATCH_CATEGORIES:
            await _show(query, *(await _odds_view(session_factory, group_id)))
            return
        label = ODDS_CATEGORIES[category][0]
        return_to = f"a:oc:{group_id}:{category}"
        await state.set_state(AdminInput.setting_value)
        await state.set_data(
            {
                "kind": "odds_batch",
                "group_id": group_id,
                "category": category,
                "return_to": return_to,
            }
        )
        await query.message.answer(
            f"请输入 <b>{escape(label)}</b> 全部项目的新返还倍数。\n"
            "这会统一修改本类倍率，但保持每个项目当前的启停状态。\n"
            "示例：<code>6.00</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_button("取消并返回", return_to)]]
            ),
        )
    elif action == "oe":
        group_id, odds_id = int(parts[2]), int(parts[3])
        category = parts[4] if len(parts) > 4 else None
        async with session_factory() as session, session.begin():
            odds = await session.get(OddsSetting, odds_id, with_for_update=True)
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if odds is None or odds.group_id != group_id or settings is None:
                await query.message.answer("赔率项目不存在。")
                return
            odds.enabled = not odds.enabled
            settings.version += 1
            category = category or _odds_category_for(odds)
            await _audit(
                session,
                admin_id,
                "odds_changed",
                group_id,
                field=f"odds:{odds.bet_type}:{odds.bet_value}:enabled",
                value=odds.enabled,
            )
        await _show(query, *(await _odds_view(session_factory, group_id, category)))
    elif action == "w":
        await _show(query, *(await _welfare_view(session_factory, int(parts[2]))))
    elif action == "st":
        group_id = int(parts[2])
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if settings:
                settings.streak_enabled = not settings.streak_enabled
                settings.version += 1
                await _audit(session, admin_id, "setting_changed", group_id, field="streak_enabled", value=settings.streak_enabled)
        await _show(query, *(await _welfare_view(session_factory, group_id)))
    elif action == "d":
        await _show(query, *(await _data_view(session_factory, int(parts[2]))))
    elif action == "hs":
        group_id = int(parts[2])
        value = min(max(int(parts[3]), TREND_MIN_POINTS), TREND_MAX_POINTS)
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if settings:
                settings.history_size = value
                settings.version += 1
                await _audit(session, admin_id, "setting_changed", group_id, field="history_size", value=value)
        await _show(query, *(await _data_view(session_factory, group_id)))
    elif action == "x":
        await _show(query, *(await _test_view(session_factory, int(parts[2]))))
    elif action == "xt":
        group_id = int(parts[2])
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if settings:
                settings.test_mode = not settings.test_mode
                settings.version += 1
                audit_action = "test_mode_enabled" if settings.test_mode else "test_mode_disabled"
                await _audit(session, admin_id, audit_action, group_id)
        await _show(query, *(await _test_view(session_factory, group_id)))
    elif action == "pl":
        await _show(query, *(await _players_view(session_factory, int(parts[2]), int(parts[3]))))
    elif action == "us":
        group_id = int(parts[2])
        await state.set_state(AdminInput.player_search)
        await state.set_data({"group_id": group_id})
        await query.message.answer(
            "请输入用户名、@用户名或 Telegram 数字ID，也可以直接转发该玩家的消息。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("取消", f"a:pl:{group_id}:0" if group_id else "a:h")]
                ]
            ),
        )
    elif action == "ug":
        await _show(query, *(await _player_groups_view(session_factory, int(parts[2]))))
    elif action == "u":
        await _show(query, *(await _player_view(session_factory, int(parts[2]), int(parts[3]), parts[4])))
    elif action == "ua":
        group_id, user_id, direction = int(parts[2]), int(parts[3]), parts[4]
        await state.set_state(AdminInput.wallet_adjustment)
        await state.set_data({"group_id": group_id, "user_id": user_id, "direction": direction})
        await query.message.answer(
            f"请输入{'上分' if direction == 'credit' else '下分'}金额和备注。\n示例：<code>100 活动奖励</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_button("取消", f"a:u:{group_id}:{user_id}:all")]]),
        )
    elif action == "uc":
        values = await state.get_data()
        if await state.get_state() != AdminInput.wallet_confirmation.state:
            await query.message.answer("本次确认已失效，请重新操作。")
            return
        group_id = int(values["group_id"])
        user_id = int(values["user_id"])
        amount = Decimal(values["amount"])
        direction = values["direction"]
        note = str(values["note"])
        async with session_factory() as session, session.begin():
            wallet = await session.scalar(
                select(Wallet)
                .where(Wallet.group_id == group_id, Wallet.user_id == user_id)
                .with_for_update()
            )
            if wallet is None:
                raise ValueError("玩家钱包不存在")
            delta = amount if direction == "credit" else -amount
            if wallet.balance + delta < 0:
                await query.message.answer(f"下分失败：当前余额只有 {wallet.balance}。")
                await state.clear()
                return
            wallet.balance += delta
            action_row = AdminAction(
                admin_user_id=admin_id,
                group_id=group_id,
                action="wallet_credit" if direction == "credit" else "wallet_debit",
                details={"user_id": user_id, "amount": str(amount), "note": note},
            )
            session.add(action_row)
            await session.flush()
            session.add(
                WalletLedger(
                    wallet_id=wallet.id,
                    idempotency_key=f"admin:{action_row.id}",
                    entry_type="admin_credit" if direction == "credit" else "admin_debit",
                    amount=delta,
                    balance_after=wallet.balance,
                    reference_type="admin_action",
                    reference_id=action_row.id,
                    note=note,
                )
            )
        await state.clear()
        await query.message.answer("操作已完成，账本和管理员日志均已记录。")
        await _show(query, *(await _player_view(session_factory, group_id, user_id, "all")))
    elif action == "ux":
        values = await state.get_data()
        await state.clear()
        await _show(query, *(await _player_view(session_factory, int(values["group_id"]), int(values["user_id"]), "all")))
    elif action == "r":
        await _show(query, *(await _ranking_view(session_factory, int(parts[2]), parts[3])))
    elif action == "rp":
        await _show(query, *(await _report_view(session_factory, int(parts[2]))))
    elif action == "l":
        await _show(query, *(await _logs_view(session_factory, int(parts[2]))))
    elif action == "bk":
        await _show(query, *(await _backup_view()))
    elif action == "bl":
        await _show(query, *(await _backup_list_view()))
    elif action == "bn":
        await query.message.answer("正在生成并加密数据库备份，请稍候。")
        try:
            filename = await _create_backup(session_factory, admin_id)
        except (OSError, RuntimeError, ValueError) as exc:
            await query.message.answer(f"备份失败：{escape(str(exc))}", parse_mode=ParseMode.HTML)
        else:
            await query.message.answer(f"备份完成：<code>{escape(filename)}</code>", parse_mode=ParseMode.HTML)
        await _show(query, *(await _backup_view()))
    elif action == "br":
        await _show(
            query,
            "<b>♻️ 恢复备份</b>\n\n恢复会先验证加密密钥和备份完整性，并自动创建恢复前快照。请在服务器项目目录执行：\n\n<code>bash scripts/linux/restore.sh backups/文件名.bwf</code>",
            InlineKeyboardMarkup(inline_keyboard=[[_button("⬅️ 返回备份", "a:bk")]]),
        )
    elif action == "rules":
        text = (
            "<b>📖 三骰玩法</b>\n\n"
            "大/小：3-10 小，11-18 大\n"
            "单/双：按和值奇偶判断\n"
            "组合：dd 大单、ds 大双、xd 小单、xs 小双\n"
            "特殊：和值3-18、顺子、任意豹子、指定豹子111至666\n\n"
            "下注示例：<code>大100、dd100、和值 10 100、顺子100、111 100</code>"
        )
        await _show(query, text, InlineKeyboardMarkup(inline_keyboard=[_home_button()]))
    elif action == "pub":
        group_id = int(parts[2])
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id)
            odds_rows = (
                await session.scalars(
                    select(OddsSetting).where(
                        OddsSetting.group_id == group_id,
                        OddsSetting.enabled.is_(True),
                    )
                )
            ).all()
            if settings is None:
                await query.message.answer("该群配置不存在。")
                return
            odds = {(row.bet_type, row.bet_value): row.payout_multiplier for row in odds_rows}
            session.add(
                OutboxMessage(
                    group_id=group_id,
                    sequence=0,
                    message_type="text",
                    payload={
                        "text": rules_text(odds, settings.minimum_bet),
                        "pin": True,
                        "button_template": "rules",
                    },
                    idempotency_key=f"admin-rules:{group_id}:{uuid.uuid4().hex}",
                )
            )
            await _audit(session, admin_id, "rules_published", group_id)
        await query.message.answer("玩法说明已加入该群发送队列。")
        await _show(query, *(await _group_view(session_factory, group_id)))
    elif action == "i":
        group_id, kind = int(parts[2]), parts[3]
        labels = {
            "bs": ("下注窗口秒数", "1 至 3600 的整数"),
            "rs": ("封盘到开奖秒数", "1 至 3600 的整数"),
            "ns": ("结算到下一局秒数", "1 至 3600 的整数"),
            "ps": ("玩家掷骰窗口秒数", "1 至 3600 的整数"),
            "min": ("最低下注", "大于0，最多两位小数"),
            "dice": ("玩家掷骰门槛", "大于0，最多两位小数"),
            "cmin": ("签到最小值", "大于等于0，最多两位小数"),
            "cmax": ("签到最大值", "大于等于0，最多两位小数"),
            "cstep": ("签到步进", "大于0，最多两位小数"),
            "history": (
                "走势期数",
                f"{TREND_MIN_POINTS} 至 {TREND_MAX_POINTS} 的整数；始终只展示最近这些期",
            ),
            "streak": ("连胜奖励档位", "格式：3=10,5=30,10=100"),
        }
        if kind in {"bs", "rs", "ns", "ps"}:
            return_to = f"a:t:{group_id}"
        elif kind in {"min", "dice"}:
            return_to = f"a:b:{group_id}"
        elif kind in {"cmin", "cmax", "cstep", "streak"}:
            return_to = f"a:w:{group_id}"
        elif kind == "history":
            return_to = f"a:d:{group_id}"
        else:
            return_to = f"a:g:{group_id}"
        await state.set_state(AdminInput.setting_value)
        await state.set_data({"kind": kind, "group_id": group_id, "return_to": return_to})
        label, hint = labels[kind]
        await query.message.answer(
            f"请输入 <b>{label}</b> 的新值。\n要求：{hint}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_button("取消并返回", return_to)]]
            ),
        )


@router.message(AdminInput.message_button, F.chat.type == ChatType.PRIVATE)
async def admin_message_button_input(
    message: Message, session_factory, state: FSMContext
) -> None:
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    values = await state.get_data()
    group_id = int(values["group_id"])
    code = str(values["template_code"])
    key, _ = MESSAGE_BUTTON_TEMPLATES[code]
    try:
        label, url = parse_message_button_input(message.text or "")
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if settings is None:
                raise ValueError("群设置不存在")
            configured = dict(settings.message_buttons or {})
            buttons = list(configured.get(key, []))
            if len(buttons) >= 8:
                raise ValueError("该消息已经配置了8个按钮")
            buttons.append({"text": label, "url": url})
            configured[key] = buttons
            settings.message_buttons = configured
            settings.version += 1
            await _audit(
                session,
                message.from_user.id,
                "message_button_added",
                group_id,
                template=key,
                text=label,
                url=url,
            )
    except ValueError as exc:
        await message.answer(f"输入有误：{escape(str(exc))}。请重新输入。")
        return
    await state.clear()
    text, markup = await _message_button_template_view(session_factory, group_id, code)
    await message.answer(
        f"✅ 按钮已保存。\n\n{text}",
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


@router.message(AdminInput.setting_value, F.chat.type == ChatType.PRIVATE)
async def admin_setting_input(message: Message, session_factory, state: FSMContext) -> None:
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    values = await state.get_data()
    kind = values["kind"]
    group_id = int(values["group_id"])
    raw = (message.text or "").strip()
    return_to = values.get("return_to", f"a:g:{group_id}")
    try:
        async with session_factory() as session, session.begin():
            settings = await session.get(GameSettings, group_id, with_for_update=True)
            if settings is None:
                raise ValueError("群设置不存在")
            field = kind
            saved: object
            if kind == "odds":
                odds = await session.get(OddsSetting, int(values["odds_id"]), with_for_update=True)
                amount = Decimal(raw).quantize(Decimal("0.01"))
                if odds is None or odds.group_id != group_id or amount <= 0:
                    raise ValueError("返还倍数必须大于0")
                odds.payout_multiplier = amount
                field = f"odds:{odds.bet_type}:{odds.bet_value}"
                saved = amount
                audit_action = "odds_changed"
            elif kind == "odds_batch":
                category = str(values["category"])
                category_config = ODDS_CATEGORIES.get(category)
                amount = Decimal(raw).quantize(Decimal("0.01"))
                if category_config is None or amount <= 0:
                    raise ValueError("返还倍数必须大于0")
                bet_types = category_config[1]
                odds_rows = (
                    await session.scalars(
                        select(OddsSetting)
                        .where(
                            OddsSetting.group_id == group_id,
                            OddsSetting.bet_type.in_(bet_types),
                        )
                        .with_for_update()
                    )
                ).all()
                if not odds_rows:
                    raise ValueError("该分类没有赔率项目")
                for odds in odds_rows:
                    odds.payout_multiplier = amount
                field = f"odds_category:{category}"
                saved = amount
                audit_action = "odds_changed"
            elif kind == "streak":
                rewards: dict[str, str] = {}
                for item in raw.replace("，", ",").split(","):
                    count_text, reward_text = item.split("=", 1)
                    count = int(count_text.strip())
                    reward = Decimal(reward_text.strip()).quantize(Decimal("0.01"))
                    if count < 1 or reward <= 0:
                        raise ValueError("连胜次数和奖励必须大于0")
                    rewards[str(count)] = str(reward)
                settings.streak_rewards = rewards
                field = "streak_rewards"
                saved = rewards
                audit_action = "setting_changed"
            elif kind in {"bs", "rs", "ns", "ps", "history"}:
                value = int(raw)
                if kind == "history":
                    if not TREND_MIN_POINTS <= value <= TREND_MAX_POINTS:
                        raise ValueError(
                            f"走势期数范围为{TREND_MIN_POINTS}至{TREND_MAX_POINTS}"
                        )
                    field = "history_size"
                else:
                    if not 1 <= value <= 3600:
                        raise ValueError("时间范围为1至3600秒")
                    field = {"bs": "betting_seconds", "rs": "rolling_seconds", "ns": "next_round_seconds", "ps": "player_dice_seconds"}[kind]
                setattr(settings, field, value)
                saved = value
                audit_action = "setting_changed"
            else:
                amount = Decimal(raw).quantize(Decimal("0.01"))
                if kind in {"min", "dice", "cstep"} and amount <= 0:
                    raise ValueError("数值必须大于0")
                if kind in {"cmin", "cmax"} and amount < 0:
                    raise ValueError("数值必须大于等于0")
                field = {
                    "min": "minimum_bet",
                    "dice": "player_dice_threshold",
                    "cmin": "checkin_min",
                    "cmax": "checkin_max",
                    "cstep": "checkin_step",
                }[kind]
                setattr(settings, field, amount)
                if settings.checkin_min > settings.checkin_max:
                    raise ValueError("签到最小值不得高于最大值")
                saved = amount
                audit_action = "setting_changed"
            settings.version += 1
            await _audit(
                session,
                message.from_user.id,
                audit_action,
                group_id,
                field=field,
                value=str(saved),
            )
    except (ValueError, InvalidOperation) as exc:
        await message.answer(f"输入有误：{escape(str(exc))}。请重新输入。", parse_mode=ParseMode.HTML)
        return
    await state.clear()
    if kind in {"odds", "odds_batch"}:
        category = str(values["category"])
        text, markup = await _odds_view(session_factory, group_id, category)
        await message.answer(
            "✅ 倍率已保存；新投注立即使用新倍率，已经受理的投注保持原倍率。\n\n"
            f"{text}",
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        return
    await message.answer(
        "设置已保存。",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_button("返回设置", return_to)]]),
    )


@router.message(AdminInput.round_search, F.chat.type == ChatType.PRIVATE)
async def admin_round_search(message: Message, session_factory, state: FSMContext) -> None:
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    public_code = parse_public_round_code(message.text or "")
    if public_code is None:
        await message.answer(
            "没有识别到 32 位期号，请检查后重新发送。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_button("取消并返回", "a:h")]]
            ),
        )
        return
    async with session_factory() as session:
        report = await load_round_audit(session, public_code)
    if report is None:
        await message.answer(
            f"没有找到期号 <code>{public_code}</code>，请确认期号来自当前机器人。",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_button("取消并返回", "a:h")]]
            ),
        )
        return
    await state.clear()
    pages = round_audit_pages(report)
    for index, page in enumerate(pages):
        markup = None
        if index == len(pages) - 1:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("继续查询期号", "a:rs")],
                    _home_button(),
                ]
            )
        await message.answer(page, parse_mode=ParseMode.HTML, reply_markup=markup)


@router.message(AdminInput.player_search, F.chat.type == ChatType.PRIVATE)
async def admin_player_search(message: Message, session_factory, state: FSMContext) -> None:
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    values = await state.get_data()
    group_id = int(values["group_id"])
    origin = getattr(message, "forward_origin", None)
    forwarded_user = getattr(origin, "sender_user", None)
    raw = (message.text or "").strip()
    async with session_factory() as session:
        query = select(User).join(Wallet, Wallet.user_id == User.id).distinct()
        if group_id:
            query = query.where(Wallet.group_id == group_id)
        if forwarded_user is not None:
            query = query.where(User.id == forwarded_user.id)
        elif raw.lstrip("-").isdigit():
            query = query.where(User.id == int(raw))
        else:
            keyword = raw.lstrip("@").strip().lower()
            query = query.where(
                or_(
                    func.lower(User.username) == keyword,
                    func.lower(User.display_name).contains(keyword),
                )
            )
        users = (await session.scalars(query.limit(20))).all()
    if not users:
        await message.answer(
            "没有找到这个玩家，请检查昵称、@用户名或数字ID后重试。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button("取消并返回", f"a:pl:{group_id}:0" if group_id else "a:h")]
                ]
            ),
        )
        return
    await state.clear()
    if len(users) == 1:
        if group_id:
            text, markup = await _player_view(session_factory, group_id, users[0].id, "all")
        else:
            text, markup = await _player_groups_view(session_factory, users[0].id)
        await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        return
    rows = [
        [
            _button(
                f"{user.display_name[:20]} · {user.id}",
                f"a:u:{group_id}:{user.id}:all" if group_id else f"a:ug:{user.id}",
            )
        ]
        for user in users
    ]
    rows.append([_button("⬅️ 返回", f"a:pl:{group_id}:0" if group_id else "a:h")])
    await message.answer("找到多个匹配玩家，请选择：", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(AdminInput.wallet_adjustment, F.chat.type == ChatType.PRIVATE)
async def admin_wallet_input(message: Message, session_factory, state: FSMContext) -> None:
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or "").strip()
    values = await state.get_data()
    try:
        amount_text, note = raw.split(maxsplit=1)
        amount = Decimal(amount_text).quantize(Decimal("0.01"))
        if amount <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        await message.answer("格式有误。请按“金额 备注”输入，例如：100 活动奖励。")
        return
    group_id, user_id = int(values["group_id"]), int(values["user_id"])
    async with session_factory() as session:
        user = await session.get(User, user_id)
        wallet = await session.scalar(
            select(Wallet).where(Wallet.group_id == group_id, Wallet.user_id == user_id)
        )
    if user is None or wallet is None:
        await state.clear()
        await message.answer("玩家钱包不存在。")
        return
    if values["direction"] == "debit" and amount > wallet.balance:
        await message.answer(f"下分金额超过当前余额 {wallet.balance}，请重新输入。")
        return
    await state.set_state(AdminInput.wallet_confirmation)
    await state.update_data(amount=str(amount), note=note)
    text = (
        "<b>请确认本次钱包操作</b>\n\n"
        f"玩家：{escape(user.display_name)}（<code>{user.id}</code>）\n"
        f"当前余额：{wallet.balance}\n"
        f"操作：{'上分' if values['direction'] == 'credit' else '下分'} {amount}\n"
        f"备注：{escape(note)}"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[_button("✅ 确认执行", "a:uc"), _button("取消", "a:ux")]]
    )
    await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)


@router.message(StateFilter(None), F.chat.type == ChatType.PRIVATE)
async def private_admin_fallback(message: Message, session_factory) -> None:
    if not is_super_admin(message.from_user.id if message.from_user else None):
        return
    text, markup = await _home_view(session_factory)
    await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)
