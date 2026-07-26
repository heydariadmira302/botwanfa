from decimal import Decimal
from io import BytesIO

from PIL import Image

from botwanfa.domain.dice import evaluate_dice
from botwanfa.presentation import (
    BetSummary,
    SettlementSummary,
    TrendPoint,
    closed_bet_text,
    load_status_animation,
    open_caption,
    render_bet_summary_pages,
    render_settlement_pages,
    render_trend_image,
    result_caption,
    rules_text,
    settlement_text,
    success_bet_text,
)


def image_size(content: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(content)) as image:
        image.verify()
    with Image.open(BytesIO(content)) as image:
        return image.size


def test_open_and_success_templates_include_required_details() -> None:
    opened = open_caption(round_number=12, betting_seconds=30, minimum_bet=Decimal(1))
    assert "期号：<code>000012</code>" in opened
    assert "30 秒" in opened
    assert "和值 10 100" in opened
    assert "任一项目有误则整条不扣分" in opened

    accepted = success_bet_text(
        display_name="<测试 & 玩家>",
        user_id=123,
        items=("大 100.00", "和值10 20.00"),
        total=Decimal(120),
        balance=Decimal(880),
    )
    assert "&lt;测试 &amp; 玩家&gt;" in accepted
    assert "ID: <code>123</code>" in accepted
    assert "大 100.00、和值10 20.00" in accepted


def test_result_caption_keeps_triple_independent_results() -> None:
    caption = result_caption(8, evaluate_dice((1, 1, 1)), "bot")
    assert "和值：<b>3</b>" in caption
    assert "小 / 单 / 小单" in caption
    assert "豹子" in caption
    assert "指定豹子111" in caption


def test_normal_close_message_keeps_player_bets_in_text() -> None:
    rows = [
        BetSummary(
            user_id=123,
            display_name="玩家甲",
            items=("大 100.00", "和值10 50.00"),
            total=Decimal(150),
        )
    ]
    text = closed_bet_text(
        round_number=12,
        rows=rows,
        bet_count=2,
        turnover=Decimal(150),
        rolling_seconds=10,
    )
    assert "本期下注玩家：1 人" in text
    assert "玩家甲" in text
    assert "投注：大 100.00、和值10 50.00" in text
    assert "机器人将在 10 秒后掷骰子" in text


def test_rules_template_uses_current_group_odds() -> None:
    odds = {
        ("big", ""): Decimal(2),
        ("small", ""): Decimal(2),
        ("odd", ""): Decimal(2),
        ("even", ""): Decimal(2),
        ("big_odd", ""): Decimal(4),
        ("big_even", ""): Decimal(4),
        ("small_odd", ""): Decimal(4),
        ("small_even", ""): Decimal(4),
        ("sum", "10"): Decimal(6),
        ("straight", ""): Decimal(9),
        ("any_triple", ""): Decimal(25),
        ("specific_triple", "111"): Decimal(151),
    }
    text = rules_text(odds, Decimal(1))
    assert "大 11-18（×2.00）" in text
    assert "顺子 123/234/345/456" in text
    assert "指定豹子 111-666" in text
    assert "豹子仍同时参与大小、单双、组合和和值" in text


def test_status_and_trend_images_are_valid_png_files() -> None:
    assert image_size(load_status_animation("open")) == (240, 120)
    assert image_size(load_status_animation("closed")) == (240, 120)
    points = []
    for number in range(1, 85):
        dice = (number % 6 + 1, (number + 1) % 6 + 1, (number + 2) % 6 + 1)
        outcome = evaluate_dice(dice)
        points.append(
            TrendPoint(
                round_number=number,
                dice=dice,
                total=outcome.total,
                is_big=outcome.is_big,
                is_odd=outcome.is_odd,
                is_straight=outcome.is_straight,
                is_triple=outcome.is_triple,
            )
        )
    assert image_size(render_trend_image(points, 84)) == (1680, 787)


def test_long_bet_and_settlement_lists_are_paginated_without_omission() -> None:
    bets = [
        BetSummary(
            user_id=1000 + index,
            display_name=f"测试玩家{index}",
            items=("大 100.00", "和值10 50.00", "顺子 20.00"),
            total=Decimal(170),
        )
        for index in range(60)
    ]
    bet_pages = render_bet_summary_pages(99, bets)
    assert len(bet_pages) >= 3
    assert all(image_size(page)[0] == 1400 for page in bet_pages)

    settlements = [
        SettlementSummary(
            user_id=1000 + index,
            display_name=f"测试玩家{index}",
            wagered=Decimal(170),
            returned=Decimal(200),
            net=Decimal(30),
            balance=Decimal(1030),
        )
        for index in range(60)
    ]
    text = settlement_text(99, settlements)
    assert all(str(1000 + index) in text for index in range(60))
    settlement_pages = render_settlement_pages(99, settlements)
    assert len(settlement_pages) == 3
    assert all(image_size(page)[0] == 1500 for page in settlement_pages)
