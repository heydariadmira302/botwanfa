from itertools import permutations, product

from botwanfa.domain.bets import BetItem, BetType
from botwanfa.domain.dice import evaluate_dice, is_winning_bet


def bet(kind: BetType, value: str | None = None) -> BetItem:
    return BetItem(kind, amount=1, value=value)


def test_all_216_combinations_have_consistent_classification() -> None:
    outcomes = [evaluate_dice(values) for values in product(range(1, 7), repeat=3)]
    assert len(outcomes) == 216
    assert all(outcome.is_big == (outcome.total >= 11) for outcome in outcomes)
    assert all(outcome.is_odd == (outcome.total % 2 == 1) for outcome in outcomes)
    assert sum(outcome.is_triple for outcome in outcomes) == 6
    assert sum(outcome.is_straight for outcome in outcomes) == 24


def test_every_permutation_of_each_straight_wins() -> None:
    for sequence in ((1, 2, 3), (2, 3, 4), (3, 4, 5), (4, 5, 6)):
        for values in permutations(sequence):
            assert is_winning_bet(bet(BetType.STRAIGHT), evaluate_dice(values))


def test_111_participates_in_all_independent_play_types() -> None:
    outcome = evaluate_dice((1, 1, 1))
    assert is_winning_bet(bet(BetType.SMALL), outcome)
    assert is_winning_bet(bet(BetType.ODD), outcome)
    assert is_winning_bet(bet(BetType.SMALL_ODD), outcome)
    assert is_winning_bet(bet(BetType.SUM, "3"), outcome)
    assert is_winning_bet(bet(BetType.ANY_TRIPLE), outcome)
    assert is_winning_bet(bet(BetType.SPECIFIC_TRIPLE, "111"), outcome)


def test_666_participates_in_all_independent_play_types() -> None:
    outcome = evaluate_dice((6, 6, 6))
    assert is_winning_bet(bet(BetType.BIG), outcome)
    assert is_winning_bet(bet(BetType.EVEN), outcome)
    assert is_winning_bet(bet(BetType.BIG_EVEN), outcome)
    assert is_winning_bet(bet(BetType.SUM, "18"), outcome)
    assert is_winning_bet(bet(BetType.ANY_TRIPLE), outcome)
    assert is_winning_bet(bet(BetType.SPECIFIC_TRIPLE, "666"), outcome)
