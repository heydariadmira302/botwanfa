from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from botwanfa.domain.dice import DiceOutcome

CANVAS = (20, 22, 27)
PANEL = (31, 35, 42)
PANEL_ALT = (38, 43, 51)
WHITE = (245, 247, 250)
MUTED = (171, 179, 190)
RED = (218, 72, 65)
GOLD = (230, 184, 92)
CYAN = (83, 179, 178)
GREEN = (73, 176, 121)
TREND_MIN_POINTS = 14
TREND_MAX_POINTS = 84
TREND_COLUMNS = 14

FONT_CANDIDATES = (
    os.environ.get("CJK_FONT", ""),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

PIPS = {
    1: ((1, 1),),
    2: ((0, 0), (2, 2)),
    3: ((0, 0), (1, 1), (2, 2)),
    4: ((0, 0), (2, 0), (0, 2), (2, 2)),
    5: ((0, 0), (2, 0), (1, 1), (0, 2), (2, 2)),
    6: ((0, 0), (0, 1), (0, 2), (2, 0), (2, 1), (2, 2)),
}

BET_LABELS = {
    "big": "大",
    "small": "小",
    "odd": "单",
    "even": "双",
    "big_odd": "大单",
    "big_even": "大双",
    "small_odd": "小单",
    "small_even": "小双",
    "sum": "和值",
    "straight": "顺子",
    "any_triple": "豹子",
    "specific_triple": "指定豹子",
}


@dataclass(frozen=True, slots=True)
class BetSummary:
    user_id: int
    display_name: str
    items: tuple[str, ...]
    total: Decimal


@dataclass(frozen=True, slots=True)
class SettlementSummary:
    user_id: int
    display_name: str
    wagered: Decimal
    returned: Decimal
    net: Decimal
    balance: Decimal
    streak_reward: Decimal = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class TrendPoint:
    round_number: int
    dice: tuple[int, int, int]
    total: int
    is_big: bool
    is_odd: bool
    is_straight: bool
    is_triple: bool


def money(value: Decimal | int | str) -> str:
    return f"{Decimal(value):,.2f}"


def round_code(round_number: int) -> str:
    return str(round_number).zfill(6)


def round_reference(group_id: int, round_number: int) -> str:
    """Return a stable, non-sequential public identifier for a group round."""
    value = f"{group_id}:{round_number}".encode("ascii")
    return hashlib.blake2s(value, digest_size=16, person=b"bwfround").hexdigest()


def _round_label(round_number: int, reference: str | None) -> str:
    return reference or round_code(round_number)


def player_mention(user_id: int, display_name: str, *, max_label: int = 16) -> str:
    label = display_name.strip() or f"玩家 {user_id}"
    if len(label) > max_label:
        label = label[: max_label - 1] + "…"
    return f'<a href="tg://user?id={user_id}">{escape(label)}</a>'


def caption_with_player_mentions(
    caption: str, players: Sequence[tuple[int, str]]
) -> str:
    if not players:
        return caption
    if len(players) <= 20:
        mentions = "、".join(player_mention(user_id, name) for user_id, name in players)
    else:
        # Names and settlement details remain visible in the image. Compact links keep
        # every bettor mentioned while the single photo caption stays under 1024 chars.
        mentions = "".join(
            f'<a href="tg://user?id={user_id}">•</a>' for user_id, _ in players
        )
    return f"{caption}\n\n<b>🔔 本期下注玩家</b>\n{mentions}"


def bet_label(bet_type: str, bet_value: str = "") -> str:
    label = BET_LABELS.get(bet_type, bet_type)
    return f"{label}{bet_value}" if bet_value else label


def bet_item_text(bet_type: str, bet_value: str, amount: Decimal) -> str:
    return f"{bet_label(bet_type, bet_value)} {money(amount)}"


def success_bet_text(
    *, display_name: str, user_id: int, items: Iterable[str], total: Decimal, balance: Decimal
) -> str:
    return (
        f"✅ {player_mention(user_id, display_name)} 投注已受理\n"
        f"投注：{escape('、'.join(items))}\n"
        f"合计扣分：<b>{money(total)}</b>　余额：<b>{money(balance)}</b>"
    )


def open_caption(
    *,
    round_number: int,
    betting_seconds: int,
    minimum_bet: Decimal,
    reference: str | None = None,
) -> str:
    return (
        f"<b>第 <code>{_round_label(round_number, reference)}</code> 期 · 开始下注</b>\n"
        f"⏳ {betting_seconds} 秒　最低下注 <b>{money(minimum_bet)}</b>\n\n"
        "<b>下注格式</b>\n"
        "大小单双：<code>大100　小100　单100　双100</code>\n"
        "组合：<code>大单100　大双100　小单100　小双100</code>\n"
        "组合缩写：<code>dd100　ds100　xd100　xs100</code>\n"
        "和值：<code>和值 10 100</code>\n"
        "特殊：<code>顺子100　豹子100　111 100</code>\n\n"
        "可在一条消息中发送多项；任一项有误，整条不扣分。"
    )


def closed_caption(
    *,
    round_number: int,
    player_count: int,
    bet_count: int,
    turnover: Decimal,
    reference: str | None = None,
) -> str:
    return (
        "<b>🚫 停止下注，等待掷骰子</b>\n"
        f"期号：<code>{_round_label(round_number, reference)}</code>\n"
        f"参与玩家：{player_count} 人　投注项目：{bet_count} 项\n"
        f"本期总流水：<b>{money(turnover)}</b>"
    )


def closed_bet_text(
    *,
    round_number: int,
    rows: Sequence[BetSummary],
    bet_count: int,
    turnover: Decimal,
    rolling_seconds: int,
    reference: str | None = None,
) -> str:
    lines = [
        "<b>🚫 停止下注，等待掷骰子</b>",
        f"期号：<code>{_round_label(round_number, reference)}</code>",
        f"本期下注玩家：{len(rows)} 人　投注项目：{bet_count} 项",
        f"本期总流水：<b>{money(turnover)}</b>",
    ]
    if not rows:
        lines.extend(("", f"无人下注，将在 {rolling_seconds} 秒后进入开奖流程。"))
        return "\n".join(lines)
    for index, row in enumerate(rows, 1):
        lines.extend(
            (
                "",
                f"{index}. {player_mention(row.user_id, row.display_name)}",
                f"投注：{escape('、'.join(row.items))}",
                f"小计：<b>{money(row.total)}</b>",
            )
        )
    lines.extend(("", f"将在 {rolling_seconds} 秒后进入掷骰流程。"))
    return "\n".join(lines)


def result_caption(
    round_number: int,
    outcome: DiceOutcome,
    source: str,
    *,
    reference: str | None = None,
) -> str:
    combination = f"{'大' if outcome.is_big else '小'}{'单' if outcome.is_odd else '双'}"
    labels = ["大" if outcome.is_big else "小", "单" if outcome.is_odd else "双", combination]
    if outcome.is_straight:
        labels.append("顺子")
    if outcome.is_triple:
        labels.extend(("豹子", f"指定豹子{outcome.triple_value}"))
    source_text = (
        "本期达标玩家掷骰"
        if source.startswith("player:")
        else "机器人掷骰"
    )
    return (
        f"<b>🎯 第 <code>{_round_label(round_number, reference)}</code> 期 · 开奖结果</b>\n"
        f"点数：<b>{outcome.dice[0]} - {outcome.dice[1]} - {outcome.dice[2]}</b>　"
        f"和值：<b>{outcome.total}</b>\n"
        f"命中：<b>{' / '.join(labels)}</b>\n"
        f"开奖方式：{source_text}"
    )


def settlement_text(
    round_number: int,
    rows: Sequence[SettlementSummary],
    *,
    reference: str | None = None,
) -> str:
    lines = [
        f"<b>💰 第 <code>{_round_label(round_number, reference)}</code> 期 · 结算完成</b>"
    ]
    if not rows:
        lines.append("\n本期无人投注。")
        return "\n".join(lines)
    total_wagered = sum((row.wagered for row in rows), Decimal("0.00"))
    total_returned = sum((row.returned for row in rows), Decimal("0.00"))
    total_net = total_returned - total_wagered
    net_sign = "+" if total_net > 0 else ""
    lines.extend(
        (
            f"参与 {len(rows)} 人　投注 <b>{money(total_wagered)}</b>　返还 <b>{money(total_returned)}</b>",
            f"玩家合计净输赢：<b>{net_sign}{money(total_net)}</b>",
        )
    )
    for index, row in enumerate(rows, 1):
        reward = f"　连胜奖励 +{money(row.streak_reward)}" if row.streak_reward > 0 else ""
        sign = "+" if row.net > 0 else ""
        state = "🟢" if row.net > 0 else ("🔴" if row.net < 0 else "⚪")
        lines.extend(
            (
                "",
                f"{index}. {state} {player_mention(row.user_id, row.display_name)}",
                (
                    f"投注 {money(row.wagered)}　返还 {money(row.returned)}　"
                    f"净输赢 <b>{sign}{money(row.net)}</b>{reward}"
                ),
                f"最终余额 <b>{money(row.balance)}</b>",
            )
        )
    return "\n".join(lines)


def result_settlement_text(
    round_number: int,
    outcome: DiceOutcome,
    source: str,
    rows: Sequence[SettlementSummary],
    *,
    reference: str | None = None,
) -> str:
    return (
        f"{result_caption(round_number, outcome, source, reference=reference)}\n\n"
        f"{settlement_text(round_number, rows, reference=reference)}"
    )


def rules_text(odds: dict[tuple[str, str], Decimal], minimum_bet: Decimal) -> str:
    def odd(kind: str, value: str = "") -> str:
        current = odds.get((kind, value)) or odds.get((kind, ""))
        return money(current) if current is not None else "未启用"

    sum_odds = "　".join(f"{value}×{odd('sum', str(value))}" for value in range(3, 19))
    triple_odds = "　".join(
        f"{value * 3}×{odd('specific_triple', str(value) * 3)}" for value in range(1, 7)
    )
    return (
        "<b>三骰玩法说明</b>\n\n"
        "每期使用三颗 Telegram 原生骰子，三颗点数相加得到和值。\n\n"
        "<b>基础玩法</b>\n"
        f"大 11-18（×{odd('big')}）　小 3-10（×{odd('small')}）\n"
        f"单（×{odd('odd')}）　双（×{odd('even')}）\n"
        f"dd 大单（×{odd('big_odd')}）　ds 大双（×{odd('big_even')}）\n"
        f"xd 小单（×{odd('small_odd')}）　xs 小双（×{odd('small_even')}）\n\n"
        "<b>特殊玩法</b>\n"
        "和值 3-18（各和值独立倍率）：\n"
        f"{sum_odds}\n"
        f"顺子 123/234/345/456，顺序不限（×{odd('straight')}）\n"
        f"任意豹子（×{odd('any_triple')}）\n"
        "指定豹子 111-666（各自独立倍率）：\n"
        f"{triple_odds}\n\n"
        "豹子仍同时参与大小、单双、组合和和值判定。\n"
        "返还额包含本金：返还额 = 投注额 × 本期倍率。\n\n"
        f"本群最低下注：<b>{money(minimum_bet)}</b>\n"
        "下注示例：<code>大100、小单100、dd100、和值 10 100、顺子100、111 100</code>\n\n"
        "常用查询：余额、签到/qd、日榜、周榜、月榜"
    )


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = False):
    candidates = list(FONT_CANDIDATES)
    if not bold:
        candidates = sorted(candidates, key=lambda item: "Bold" in item or "bd." in item)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_die(
    draw: ImageDraw.ImageDraw, x: int, y: int, size: int, value: int, *, dark: bool = False
) -> None:
    fill = PANEL_ALT if dark else WHITE
    pip = WHITE if dark else (35, 38, 45)
    radius = max(3, size // 7)
    draw.rounded_rectangle(
        (x, y, x + size, y + size), radius=radius, fill=fill, outline=MUTED, width=max(1, size // 30)
    )
    margin = size * 0.27
    gap = (size - 2 * margin) / 2
    pip_radius = max(1, int(size * 0.075))
    for column, row in PIPS[value]:
        center_x = x + margin + column * gap
        center_y = y + margin + row * gap
        draw.ellipse(
            (
                center_x - pip_radius,
                center_y - pip_radius,
                center_x + pip_radius,
                center_y + pip_radius,
            ),
            fill=pip,
        )


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


@lru_cache(maxsize=2)
def load_status_animation(mode: str) -> bytes:
    if mode not in {"open", "closed"}:
        raise ValueError("unknown status banner mode")
    filename = "betting-open.gif" if mode == "open" else "betting-closed.gif"
    return (Path(__file__).with_name("assets") / filename).read_bytes()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _header(
    draw: ImageDraw.ImageDraw,
    *,
    title: str,
    subtitle: str,
    width: int,
    accent=RED,
) -> None:
    draw.rectangle((0, 0, width, 150), fill=PANEL)
    draw.rectangle((0, 0, 16, 150), fill=accent)
    draw.text((55, 33), title, font=_font(48, True), fill=WHITE)
    draw.text((58, 98), subtitle, font=_font(24), fill=MUTED)


def render_bet_summary_pages(
    round_number: int,
    rows: Sequence[BetSummary],
    reference: str | None = None,
) -> list[bytes]:
    width = 1400
    item_font = _font(25)
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    prepared = []
    for row in rows:
        item_lines = _wrap(scratch, "  ·  ".join(row.items), item_font, width - 110)
        prepared.append((row, item_lines, 74 + len(item_lines) * 34))
    pages: list[list[tuple[BetSummary, list[str], int]]] = []
    current = []
    current_height = 0
    for prepared_row in prepared:
        if current and current_height + prepared_row[2] > 1750:
            pages.append(current)
            current = []
            current_height = 0
        current.append(prepared_row)
        current_height += prepared_row[2]
    if current or not pages:
        pages.append(current)
    total_turnover = sum((row.total for row in rows), Decimal("0.00"))
    output = []
    for page_index, page in enumerate(pages, 1):
        content_height = sum(item[2] for item in page)
        height = max(520, 220 + content_height)
        image = Image.new("RGB", (width, height), CANVAS)
        draw = ImageDraw.Draw(image)
        _header(
            draw,
            title=f"第 {_round_label(round_number, reference)} 期  停止下注",
            subtitle=(
                f"共 {len(rows)} 位玩家  ·  总流水 {money(total_turnover)}  ·  "
                f"第 {page_index}/{len(pages)} 页"
            ),
            width=width,
            accent=CYAN,
        )
        if not page:
            draw.text((width // 2, 300), "本期无人投注", font=_font(42, True), fill=MUTED, anchor="ma")
        y = 178
        for index, (row, item_lines, block_height) in enumerate(page):
            absolute_index = sum(len(item) for item in pages[: page_index - 1]) + index + 1
            fill = PANEL if absolute_index % 2 else PANEL_ALT
            draw.rounded_rectangle((35, y, width - 35, y + block_height - 10), radius=8, fill=fill)
            name = row.display_name[:28]
            draw.text(
                (60, y + 16),
                f"{absolute_index:02d}  {name}",
                font=_font(28, True),
                fill=WHITE,
            )
            draw.text(
                (width - 60, y + 16),
                f"小计 {money(row.total)}",
                font=_font(27, True),
                fill=GOLD,
                anchor="ra",
            )
            for line_index, line in enumerate(item_lines):
                draw.text((82, y + 56 + line_index * 34), line, font=item_font, fill=MUTED)
            y += block_height
        output.append(_png(image))
    return output


def render_settlement_pages(
    round_number: int,
    rows: Sequence[SettlementSummary],
    reference: str | None = None,
) -> list[bytes]:
    width = 1500
    per_page = 22
    chunks = [list(rows[index : index + per_page]) for index in range(0, len(rows), per_page)] or [[]]
    output = []
    for page_index, chunk in enumerate(chunks, 1):
        height = max(530, 235 + len(chunk) * 78)
        image = Image.new("RGB", (width, height), CANVAS)
        draw = ImageDraw.Draw(image)
        _header(
            draw,
            title=f"第 {_round_label(round_number, reference)} 期  全员结算",
            subtitle=f"投注 / 返还 / 净输赢 / 最终余额  ·  第 {page_index}/{len(chunks)} 页",
            width=width,
            accent=GOLD,
        )
        headings = ((55, "玩家"), (650, "投注"), (850, "返还"), (1050, "净输赢"), (1280, "余额"))
        for x, heading in headings:
            draw.text((x, 172), heading, font=_font(24, True), fill=MUTED)
        if not chunk:
            draw.text((width // 2, 335), "本期无人投注", font=_font(42, True), fill=MUTED, anchor="ma")
        y = 218
        for index, row in enumerate(chunk):
            absolute_index = (page_index - 1) * per_page + index + 1
            fill = PANEL if absolute_index % 2 else PANEL_ALT
            draw.rectangle((35, y, width - 35, y + 66), fill=fill)
            draw.text(
                (55, y + 15),
                f"{absolute_index:02d}  {row.display_name[:20]}",
                font=_font(24, True),
                fill=WHITE,
            )
            draw.text((650, y + 15), money(row.wagered), font=_font(24), fill=WHITE)
            draw.text((850, y + 15), money(row.returned), font=_font(24), fill=WHITE)
            net_color = GREEN if row.net > 0 else (RED if row.net < 0 else MUTED)
            sign = "+" if row.net > 0 else ""
            draw.text((1050, y + 15), f"{sign}{money(row.net)}", font=_font(24, True), fill=net_color)
            draw.text((1280, y + 15), money(row.balance), font=_font(24, True), fill=GOLD)
            y += 78
        output.append(_png(image))
    return output


def render_settlement_image(
    round_number: int,
    rows: Sequence[SettlementSummary],
    reference: str | None = None,
) -> bytes:
    width = 1500
    height = max(530, 235 + len(rows) * 78)
    image = Image.new("RGB", (width, height), CANVAS)
    draw = ImageDraw.Draw(image)
    total_wagered = sum((row.wagered for row in rows), Decimal("0.00"))
    total_returned = sum((row.returned for row in rows), Decimal("0.00"))
    _header(
        draw,
        title=f"第 {_round_label(round_number, reference)} 期  全员结算",
        subtitle=(
            f"共 {len(rows)} 位玩家  ·  投注 {money(total_wagered)}  ·  "
            f"返还 {money(total_returned)}"
        ),
        width=width,
        accent=GOLD,
    )
    headings = ((55, "玩家"), (650, "投注"), (850, "返还"), (1050, "净输赢"), (1280, "余额"))
    for x, heading in headings:
        draw.text((x, 172), heading, font=_font(24, True), fill=MUTED)
    if not rows:
        draw.text(
            (width // 2, 335),
            "本期无人投注",
            font=_font(42, True),
            fill=MUTED,
            anchor="ma",
        )
    y = 218
    for index, row in enumerate(rows, 1):
        fill = PANEL if index % 2 else PANEL_ALT
        draw.rectangle((35, y, width - 35, y + 66), fill=fill)
        draw.text(
            (55, y + 15),
            f"{index:02d}  {row.display_name[:20]}",
            font=_font(24, True),
            fill=WHITE,
        )
        draw.text((650, y + 15), money(row.wagered), font=_font(24), fill=WHITE)
        draw.text((850, y + 15), money(row.returned), font=_font(24), fill=WHITE)
        net_color = GREEN if row.net > 0 else (RED if row.net < 0 else MUTED)
        sign = "+" if row.net > 0 else ""
        draw.text(
            (1050, y + 15),
            f"{sign}{money(row.net)}",
            font=_font(24, True),
            fill=net_color,
        )
        draw.text((1280, y + 15), money(row.balance), font=_font(24, True), fill=GOLD)
        y += 78
    return _png(image)


def render_trend_image(points: Sequence[TrendPoint], current_round: int) -> bytes:
    columns = TREND_COLUMNS
    visible = list(points)[-TREND_MAX_POINTS:]
    row_count = max(1, (len(visible) + columns - 1) // columns)
    width = 1680
    start_x = 35
    start_y = 175
    gap = 8
    cell_width = (width - start_x * 2 - gap * (columns - 1)) // columns
    row_height = 156
    height = start_y + row_count * row_height + 58
    image = Image.new("RGB", (width, height), CANVAS)
    draw = ImageDraw.Draw(image)
    _header(
        draw,
        title="三骰开奖走势",
        subtitle=f"最近 {len(visible)} 期  ·  从左到右、从上到下  ·  红框为本期",
        width=width,
        accent=RED,
    )
    for index, point in enumerate(visible):
        row, column = divmod(index, columns)
        x = start_x + column * (cell_width + gap)
        y = start_y + row * row_height
        is_current = point.round_number == current_round
        fill = PANEL_ALT if (row + column) % 2 else PANEL
        bottom = y + row_height - 10
        draw.rounded_rectangle((x, y, x + cell_width, bottom), radius=8, fill=fill)
        if is_current:
            draw.rounded_rectangle(
                (x, y, x + cell_width, bottom), radius=8, outline=RED, width=4
            )
        die_size = 25
        die_gap = 4
        dice_width = die_size * 3 + die_gap * 2
        dice_x = x + (cell_width - dice_width) // 2
        for die_index, value in enumerate(point.dice):
            _draw_die(draw, dice_x + die_index * (die_size + die_gap), y + 14, die_size, value)
        draw.text(
            (x + cell_width // 2, y + 52),
            f"和值 {point.total}",
            font=_font(20, True),
            fill=WHITE,
            anchor="ma",
        )
        combination = f"{'大' if point.is_big else '小'}{'单' if point.is_odd else '双'}"
        draw.text(
            (x + cell_width // 2, y + 82),
            combination,
            font=_font(22, True),
            fill=RED if point.is_big else CYAN,
            anchor="ma",
        )
        special = "豹子" if point.is_triple else ("顺子" if point.is_straight else "普通")
        draw.text(
            (x + cell_width // 2, y + 112),
            special,
            font=_font(17, True),
            fill=GOLD if point.is_triple or point.is_straight else MUTED,
            anchor="ma",
        )
    draw.text(
        (35, height - 38),
        "每格：三颗骰子 / 和值 / 大小单双组合 / 顺子或豹子；固定展示最近结果",
        font=_font(20),
        fill=MUTED,
    )
    return _png(image)
