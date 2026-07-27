from decimal import Decimal

from botwanfa.apps.admin import (
    _odds_items_markup,
    _odds_menu_markup,
    admin_menu_markup,
    parse_message_button_input,
    parse_public_round_code,
    round_audit_pages,
)
from botwanfa.apps.sender import template_message_markup
from botwanfa.db.models import OddsSetting
from botwanfa.services.round_audit import (
    BetAuditItem,
    PlayerRoundAudit,
    RoundAuditReport,
)


def odds_row(
    row_id: int, bet_type: str, bet_value: str = "", *, enabled: bool = True
) -> OddsSetting:
    return OddsSetting(
        id=row_id,
        group_id=-100123456,
        bet_type=bet_type,
        bet_value=bet_value,
        payout_multiplier=Decimal("6.00"),
        enabled=enabled,
    )


def all_odds() -> list[OddsSetting]:
    rows = [
        odds_row(1, "big"),
        odds_row(2, "small"),
        odds_row(3, "odd"),
        odds_row(4, "even"),
        odds_row(5, "big_odd"),
        odds_row(6, "big_even"),
        odds_row(7, "small_odd"),
        odds_row(8, "small_even"),
        odds_row(9, "straight"),
        odds_row(10, "any_triple"),
    ]
    rows.extend(odds_row(10 + value, "sum", str(value)) for value in range(3, 19))
    rows.extend(
        odds_row(30 + value, "specific_triple", str(value) * 3)
        for value in range(1, 7)
    )
    return rows


def callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]


def test_main_player_actions_take_different_paths() -> None:
    markup = admin_menu_markup()
    by_label = {
        button.text: button.callback_data
        for row in markup.inline_keyboard
        for button in row
    }
    assert by_label["🔎 查询玩家"] == "a:us:0"
    assert by_label["💳 玩家上下分"] == "a:gl:0:players"
    assert by_label["🛠 平滑更新"] == "a:dr"
    assert by_label["🔍 期号查账"] == "a:rs"


def test_round_code_is_extracted_from_a_complete_result_message() -> None:
    code = "da8ea0fba936e6debb130ef5794f0d0c"
    assert parse_public_round_code(f"🎯 第 {code} 期 · 开奖及结算") == code
    assert parse_public_round_code("没有期号") is None


def test_round_audit_lists_each_bet_and_player_profit() -> None:
    code = "da8ea0fba936e6debb130ef5794f0d0c"
    report = RoundAuditReport(
        public_code=code,
        group_id=-100123,
        group_title="测试群",
        status="completed",
        dice=(6, 5, 6),
        dice_source="bot",
        players=(
            PlayerRoundAudit(
                user_id=123,
                display_name="测试玩家",
                items=(
                    BetAuditItem(
                        bet_type="big",
                        bet_value="",
                        amount=Decimal("100.00"),
                        odds=Decimal("2.00"),
                        won=True,
                        payout=Decimal("200.00"),
                    ),
                    BetAuditItem(
                        bet_type="sum",
                        bet_value="10",
                        amount=Decimal("50.00"),
                        odds=Decimal("6.00"),
                        won=False,
                        payout=Decimal("0.00"),
                    ),
                ),
                wagered=Decimal("150.00"),
                returned=Decimal("200.00"),
                net=Decimal("50.00"),
                balance_after=Decimal("1050.00"),
                streak_reward=Decimal("0.00"),
            ),
        ),
    )
    text = "\n".join(round_audit_pages(report))
    assert code in text
    assert "6 - 5 - 6（和值 17）" in text
    assert "大 100.00 × 2.00 · 赢 · 返还 200.00" in text
    assert "和值10 50.00 × 6.00 · 输 · 返还 0.00" in text
    assert "净输赢 <b>+50.00</b>" in text
    assert "结后余额：<b>1,050.00</b>" in text


def test_round_audit_paginates_without_omitting_players() -> None:
    players = tuple(
        PlayerRoundAudit(
            user_id=1000 + index,
            display_name=f"玩家{index}",
            items=(
                BetAuditItem(
                    bet_type="small_even",
                    bet_value="",
                    amount=Decimal("100.00"),
                    odds=Decimal("4.00"),
                    won=False,
                    payout=Decimal("0.00"),
                ),
            ),
            wagered=Decimal("100.00"),
            returned=Decimal("0.00"),
            net=Decimal("-100.00"),
            balance_after=Decimal("900.00"),
            streak_reward=Decimal("0.00"),
        )
        for index in range(60)
    )
    report = RoundAuditReport(
        public_code="a" * 32,
        group_id=-100123,
        group_title="分页测试群",
        status="completed",
        dice=(1, 2, 3),
        dice_source="player:1000",
        players=players,
    )
    pages = round_audit_pages(report)
    combined = "\n".join(pages)
    assert len(pages) > 1
    assert all(len(page) <= 3800 for page in pages)
    assert all(f"tg://user?id={1000 + index}" in combined for index in range(60))


def test_odds_menu_has_four_categories_without_page_navigation() -> None:
    markup = _odds_menu_markup(-100123456, all_odds())
    values = callbacks(markup)
    assert values[:4] == [
        "a:oc:-100123456:basic",
        "a:oc:-100123456:combo",
        "a:oc:-100123456:sum",
        "a:oc:-100123456:special",
    ]
    assert not any("下一页" in button.text for row in markup.inline_keyboard for button in row)


def test_all_sixteen_sum_odds_are_reachable_on_one_view() -> None:
    sum_rows = [item for item in all_odds() if item.bet_type == "sum"]
    markup = _odds_items_markup(-100123456, sum_rows, "sum")
    item_callbacks = [value for value in callbacks(markup) if value.startswith("a:oi:")]
    assert len(item_callbacks) == 16
    assert len(set(item_callbacks)) == 16
    assert "a:ob:-100123456:sum" in callbacks(markup)


def test_every_odds_item_is_reachable_without_pagination() -> None:
    rows = all_odds()
    category_types = {
        "basic": {"big", "small", "odd", "even"},
        "combo": {"big_odd", "big_even", "small_odd", "small_even"},
        "sum": {"sum"},
        "special": {"straight", "any_triple", "specific_triple"},
    }
    reachable = []
    for category, bet_types in category_types.items():
        markup = _odds_items_markup(
            -100123456,
            [item for item in rows if item.bet_type in bet_types],
            category,
        )
        values = callbacks(markup)
        reachable.extend(value for value in values if value.startswith("a:oi:"))
        assert not any("a:ol:" in value for value in values)
    assert len(reachable) == len(rows) == 32
    assert len(set(reachable)) == 32


def test_special_odds_do_not_offer_dangerous_batch_update() -> None:
    special = [
        item
        for item in all_odds()
        if item.bet_type in {"straight", "any_triple", "specific_triple"}
    ]
    markup = _odds_items_markup(-100123456, special, "special")
    assert not any(value.startswith("a:ob:") for value in callbacks(markup))


def test_odds_callbacks_fit_telegram_limit() -> None:
    rows = all_odds()
    markups = [_odds_menu_markup(-1001234567890, rows)]
    markups.extend(
        _odds_items_markup(
            -1001234567890,
            [item for item in rows if item.bet_type == category_type],
            category,
        )
        for category, category_type in (
            ("basic", "big"),
            ("combo", "big_odd"),
            ("sum", "sum"),
            ("special", "specific_triple"),
        )
    )
    assert all(len(value.encode()) <= 64 for markup in markups for value in callbacks(markup))


def test_message_button_input_and_two_column_markup() -> None:
    label, url = parse_message_button_input("充值提现\nhttps://example.com/recharge")
    assert label == "充值提现"
    assert url == "https://example.com/recharge"
    markup = template_message_markup(
        {
            "round_open": [
                {"text": "充值", "url": "https://example.com/1"},
                {"text": "客服", "url": "https://example.com/2"},
                {"text": "频道", "url": "https://example.com/3"},
            ]
        },
        "round_open",
    )
    assert markup is not None
    assert [len(row) for row in markup.inline_keyboard] == [2, 1]
    assert markup.inline_keyboard[0][0].url == "https://example.com/1"
