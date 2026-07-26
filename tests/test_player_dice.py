from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from botwanfa.apps.bot import append_player_dice_roll
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


def test_default_threshold_selects_highest_bettor_and_empty_round_selects_nobody() -> None:
    batches = [batch(1, 10, "20", 1), batch(2, 20, "50", 2), batch(3, 10, "40", 3)]
    assert choose_player_dice_candidate(batches, Decimal("0.01")) == 10
    assert choose_player_dice_candidate([], Decimal("0.01")) is None


def test_player_dice_rolls_persist_times_and_only_accept_first_three() -> None:
    snapshot = {
        "player_dice_values": [],
        "player_dice_message_ids": [],
        "player_dice_times": [],
    }
    started = datetime(2026, 1, 1, tzinfo=UTC)
    for position, value in enumerate((4, 2, 6), 1):
        updated = append_player_dice_roll(
            snapshot,
            message_id=100 + position,
            value=value,
            accepted_at=started + timedelta(seconds=position),
        )
        assert updated is not None
        snapshot = updated
    assert snapshot["player_dice_values"] == [4, 2, 6]
    assert snapshot["player_dice_message_ids"] == [101, 102, 103]
    assert len(snapshot["player_dice_times"]) == 3
    assert append_player_dice_roll(
        snapshot,
        message_id=104,
        value=1,
        accepted_at=started + timedelta(seconds=4),
    ) is None
    assert append_player_dice_roll(
        snapshot,
        message_id=103,
        value=6,
        accepted_at=started + timedelta(seconds=3),
    ) is None
