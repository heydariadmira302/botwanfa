from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class BetType(StrEnum):
    BIG = "big"
    SMALL = "small"
    ODD = "odd"
    EVEN = "even"
    BIG_ODD = "big_odd"
    BIG_EVEN = "big_even"
    SMALL_ODD = "small_odd"
    SMALL_EVEN = "small_even"
    SUM = "sum"
    STRAIGHT = "straight"
    ANY_TRIPLE = "any_triple"
    SPECIFIC_TRIPLE = "specific_triple"


@dataclass(frozen=True, slots=True)
class BetItem:
    bet_type: BetType
    amount: Decimal
    value: str | None = None
    source: str = ""


class BetParseError(ValueError):
    def __init__(self, item: str, reason: str):
        self.item = item
        self.reason = reason
        super().__init__(f"{item}: {reason}")


_LABELS = {
    "大": BetType.BIG,
    "小": BetType.SMALL,
    "单": BetType.ODD,
    "双": BetType.EVEN,
    "dd": BetType.BIG_ODD,
    "ds": BetType.BIG_EVEN,
    "xd": BetType.SMALL_ODD,
    "xs": BetType.SMALL_EVEN,
    "顺子": BetType.STRAIGHT,
    "豹子": BetType.ANY_TRIPLE,
}
_SEPARATOR = re.compile(r"^[\s,，、;；]+$")
_ITEM = re.compile(
    r"(?P<sum>和值\s*(?P<sum_value>(?:[3-9]|1[0-8])))\s+(?P<sum_amount>[1-9]\d*)"
    r"|(?P<triple>111|222|333|444|555|666)\s+(?P<triple_amount>[1-9]\d*)"
    r"|(?P<label>顺子|豹子|dd|ds|xd|xs|大|小|单|双)\s*(?P<amount>[1-9]\d*)",
    re.IGNORECASE,
)


def parse_bets(text: str) -> list[BetItem]:
    """Parse the whole message and reject the batch when any fragment is invalid."""
    normalized = text.strip()
    if not normalized:
        raise BetParseError("", "下注内容为空")

    result: list[BetItem] = []
    cursor = 0
    for match in _ITEM.finditer(normalized):
        gap = normalized[cursor : match.start()]
        if gap and not _SEPARATOR.fullmatch(gap):
            raise BetParseError(gap.strip(), "无法识别的下注项目")

        source = match.group(0)
        if match.group("sum"):
            result.append(
                BetItem(
                    BetType.SUM,
                    Decimal(match.group("sum_amount")),
                    match.group("sum_value"),
                    source,
                )
            )
        elif match.group("triple"):
            result.append(
                BetItem(
                    BetType.SPECIFIC_TRIPLE,
                    Decimal(match.group("triple_amount")),
                    match.group("triple"),
                    source,
                )
            )
        else:
            label = match.group("label").lower()
            result.append(BetItem(_LABELS[label], Decimal(match.group("amount")), None, source))
        cursor = match.end()

    tail = normalized[cursor:]
    if tail and not _SEPARATOR.fullmatch(tail):
        reason = "和值范围为3至18" if tail.strip().startswith("和值") else "无法识别的下注项目"
        raise BetParseError(tail.strip(), reason)
    if not result:
        raise BetParseError(normalized, "未找到有效下注项目")
    return result
