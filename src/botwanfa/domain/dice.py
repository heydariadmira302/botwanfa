from __future__ import annotations

from dataclasses import dataclass

from botwanfa.domain.bets import BetItem, BetType


@dataclass(frozen=True, slots=True)
class DiceOutcome:
    dice: tuple[int, int, int]
    total: int
    is_big: bool
    is_odd: bool
    is_straight: bool
    is_triple: bool
    triple_value: str | None

    @property
    def combination(self) -> BetType:
        if self.is_big:
            return BetType.BIG_ODD if self.is_odd else BetType.BIG_EVEN
        return BetType.SMALL_ODD if self.is_odd else BetType.SMALL_EVEN


def evaluate_dice(dice: tuple[int, int, int]) -> DiceOutcome:
    if len(dice) != 3 or any(value < 1 or value > 6 for value in dice):
        raise ValueError("dice must contain exactly three values from 1 to 6")
    total = sum(dice)
    ordered = sorted(dice)
    is_triple = len(set(dice)) == 1
    return DiceOutcome(
        dice=dice,
        total=total,
        is_big=total >= 11,
        is_odd=total % 2 == 1,
        is_straight=(
            len(set(dice)) == 3 and ordered[1] == ordered[0] + 1 and ordered[2] == ordered[1] + 1
        ),
        is_triple=is_triple,
        triple_value=str(dice[0]) * 3 if is_triple else None,
    )


def is_winning_bet(bet: BetItem, outcome: DiceOutcome) -> bool:
    checks = {
        BetType.BIG: outcome.is_big,
        BetType.SMALL: not outcome.is_big,
        BetType.ODD: outcome.is_odd,
        BetType.EVEN: not outcome.is_odd,
        BetType.BIG_ODD: outcome.combination == BetType.BIG_ODD,
        BetType.BIG_EVEN: outcome.combination == BetType.BIG_EVEN,
        BetType.SMALL_ODD: outcome.combination == BetType.SMALL_ODD,
        BetType.SMALL_EVEN: outcome.combination == BetType.SMALL_EVEN,
        BetType.STRAIGHT: outcome.is_straight,
        BetType.ANY_TRIPLE: outcome.is_triple,
        BetType.SPECIFIC_TRIPLE: outcome.triple_value == bet.value,
        BetType.SUM: outcome.total == int(bet.value or 0),
    }
    return checks[bet.bet_type]
