from botwanfa.services.drain import ROUND_OUTBOX_TYPES, DrainProgress


def test_drain_is_ready_only_when_rounds_and_critical_messages_are_clear() -> None:
    assert DrainProgress().ready is True
    assert DrainProgress(active_rounds=1).ready is False
    assert DrainProgress(pending_round_messages=1).ready is False
    assert DrainProgress(failed_round_messages=1).ready is False


def test_drain_waits_for_every_round_completion_phase() -> None:
    progress = DrainProgress(
        active_rounds=5,
        betting_rounds=1,
        waiting_player_rounds=1,
        rolling_rounds=1,
        settling_rounds=1,
        blocked_rounds=1,
    )
    assert progress.active_rounds == 5
    assert progress.ready is False


def test_critical_outbox_types_cover_the_complete_round_delivery() -> None:
    assert {
        "round_open",
        "round_closed",
        "player_dice_invite",
        "player_dice_ack",
        "dice_round",
        "round_result",
        "trend_result",
        "settlement_summary",
    } == set(ROUND_OUTBOX_TYPES)
