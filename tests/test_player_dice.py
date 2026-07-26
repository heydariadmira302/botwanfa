from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from botwanfa.apps.scheduler import choose_player_dice_candidate


def batch(batch_id: int, user_id: int, amount: str, seconds: int):
    return SimpleNamespace(
        id=batch_id,
        user_id=user_id,
        total_amount=Decimal(amount),
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
    )


def test_player_with_highest_total_gets_dice_priority() -> None:
    batches = [batch(1, 10, "100", 1), batch(2, 20, "150", 2)]
    assert choose_player_dice_candidate(batches, Decimal(100)) == 20


def test_equal_total_uses_earliest_threshold_time() -> None:
    batches = [
        batch(1, 10, "60", 1),
        batch(2, 20, "100", 2),
        batch(3, 10, "40", 3),
    ]
    assert choose_player_dice_candidate(batches, Decimal(100)) == 20


def test_no_player_reaches_dice_threshold() -> None:
    assert choose_player_dice_candidate([batch(1, 10, "99", 1)], Decimal(100)) is None
