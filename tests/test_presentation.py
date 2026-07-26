import re
from decimal import Decimal
from html import unescape
from io import BytesIO

from PIL import Image

from botwanfa.domain.dice import evaluate_dice
from botwanfa.presentation import (
    BetSummary,
    SettlementSummary,
    TrendPoint,
    caption_with_player_mentions,
    closed_bet_text,
    load_status_animation,
    open_caption,
    render_bet_summary_pages,
    render_round_result_image,
    render_settlement_pages,
    render_trend_image,
    result_caption,
    round_code,
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
    assert "第 <code>000012</code> 期" in opened
    assert "30 秒" in opened
    assert "和值 10 100" in opened
    assert "任一项有误，整条不扣分" in opened

    accepted = success_bet_text(
        display_name="<测试 & 玩家>",
        user_id=123,
        items=("大 100.00", "和值10 20.00"),
        total=Decimal(120),
        balance=Decimal(880),
    )
    assert "&lt;测试 &amp; 玩家&gt;" in accepted
    assert "tg://user?id=123" in accepted
    assert "ID:" not in accepted
    assert "大 100.00、和值10 20.00" in accepted


def test_result_caption_keeps_triple_independent_results() -> None:
    caption = result_caption(8, evaluate_dice((1, 1, 1)), "bot")
    assert "和值：<b>3</b>" in caption
    assert "小 / 单 / 小单" in caption
    assert "豹子" in caption
    assert "指定豹子111" in caption


def test_round_codes_remain_unambiguous_after_ten_thousand_rounds() -> None:
    assert round_code(8) == "000008"
    assert round_code(10_000) == "010000"
    assert round_code(1_000_000) == "1000000"


def test_result_mentions_stay_in_one_caption_for_small_and_large_groups() -> None:
    base = result_caption(10_000, evaluate_dice((3, 4, 5)), "bot")
    caption = caption_with_player_mentions(base, [(1, "玩家甲"), (2, "玩家乙")])
    assert "tg://user?id=1" in caption
    assert "玩家甲" in caption

    players = [(1000 + index, f"玩家{index}") for index in range(120)]
    caption = caption_with_player_mentions(base, players)
    visible_caption = unescape(re.sub(r"<[^>]+>", "", caption))
    assert all(f"tg://user?id={user_id}" in caption for user_id, _ in players)
    assert caption.count("•") == len(players)
    assert len(visible_caption) <= 1024


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
    assert "tg://user?id=123" in text
    assert "ID:" not in text
    assert "投注：大 100.00、和值10 50.00" in text
    assert "将在 10 秒后进入掷骰流程" in text


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
    for number in range(9917, 10001):
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
    assert image_size(render_trend_image(points, 10000)) == (1080, 2393)


def test_trend_image_never_grows_beyond_the_rolling_window() -> None:
    points = []
    for number in range(9801, 10001):
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
    assert image_size(render_trend_image(points, 10000)) == (1080, 2393)


def test_round_result_combines_trend_and_every_player_settlement_in_one_image() -> None:
    points = []
    for number in range(9917, 10001):
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
    settlements = [
        SettlementSummary(
            user_id=1000 + index,
            display_name=f"测试玩家{index}",
            wagered=Decimal(100),
            returned=Decimal(200),
            net=Decimal(100),
            balance=Decimal(1100),
        )
        for index in range(3)
    ]
    image = render_round_result_image(points, 10000, settlements)
    assert image_size(image) == (1500, 2802)


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
    assert all(f"tg://user?id={1000 + index}" in text for index in range(60))
    assert "ID:" not in text
    settlement_pages = render_settlement_pages(99, settlements)
    assert len(settlement_pages) == 3
    assert all(image_size(page)[0] == 1500 for page in settlement_pages)
