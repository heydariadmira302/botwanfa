from decimal import Decimal

import pytest

from botwanfa.domain.bets import BetParseError, BetType, parse_bets


def test_parse_all_supported_forms_in_one_atomic_message() -> None:
    items = parse_bets(
        "大100 小 20\n单30 双 40 dd50 ds 60 xd70 xs 80 和值10 90 顺子100 豹子 110 111 120 666 130"
    )
    assert [item.bet_type for item in items] == [
        BetType.BIG,
        BetType.SMALL,
        BetType.ODD,
        BetType.EVEN,
        BetType.BIG_ODD,
        BetType.BIG_EVEN,
        BetType.SMALL_ODD,
        BetType.SMALL_EVEN,
        BetType.SUM,
        BetType.STRAIGHT,
        BetType.ANY_TRIPLE,
        BetType.SPECIFIC_TRIPLE,
        BetType.SPECIFIC_TRIPLE,
    ]
    assert sum((item.amount for item in items), Decimal(0)) == Decimal(1000)


@pytest.mark.parametrize("text", ["和值 2 100", "和值 19 100", "大0", "大100 未知20"])
def test_rejects_whole_message_when_any_item_is_invalid(text: str) -> None:
    with pytest.raises(BetParseError):
        parse_bets(text)


def test_supports_punctuation_and_newlines() -> None:
    items = parse_bets("大100，小100；\n顺子 20、豹子20")
    assert len(items) == 4
