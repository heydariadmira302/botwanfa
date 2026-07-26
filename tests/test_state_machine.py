from itertools import pairwise

import pytest

from botwanfa.domain.state_machine import RoundStatus, assert_transition


def test_happy_path_transitions() -> None:
    path = [
        RoundStatus.WAITING,
        RoundStatus.BETTING,
        RoundStatus.CLOSED,
        RoundStatus.BOT_ROLLING,
        RoundStatus.SETTLING,
        RoundStatus.COMPLETED,
    ]
    for current, target in pairwise(path):
        assert_transition(current, target)


def test_completed_round_cannot_be_settled_again() -> None:
    with pytest.raises(ValueError):
        assert_transition(RoundStatus.COMPLETED, RoundStatus.SETTLING)
