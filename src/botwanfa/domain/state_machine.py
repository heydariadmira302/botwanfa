from enum import StrEnum


class RoundStatus(StrEnum):
    WAITING = "waiting"
    BETTING = "betting"
    CLOSED = "closed"
    WAITING_FOR_PLAYER_DICE = "waiting_for_player_dice"
    BOT_ROLLING = "bot_rolling"
    SETTLING = "settling"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


_ALLOWED = {
    RoundStatus.WAITING: {RoundStatus.BETTING, RoundStatus.PAUSED},
    RoundStatus.BETTING: {RoundStatus.CLOSED, RoundStatus.PAUSED, RoundStatus.FAILED},
    RoundStatus.CLOSED: {
        RoundStatus.WAITING_FOR_PLAYER_DICE,
        RoundStatus.BOT_ROLLING,
        RoundStatus.PAUSED,
        RoundStatus.FAILED,
    },
    RoundStatus.WAITING_FOR_PLAYER_DICE: {
        RoundStatus.BOT_ROLLING,
        RoundStatus.SETTLING,
        RoundStatus.FAILED,
    },
    RoundStatus.BOT_ROLLING: {RoundStatus.SETTLING, RoundStatus.FAILED},
    RoundStatus.SETTLING: {
        RoundStatus.COMPLETED,
        RoundStatus.MANUAL_REVIEW,
        RoundStatus.FAILED,
    },
    RoundStatus.COMPLETED: set(),
    RoundStatus.PAUSED: {RoundStatus.WAITING, RoundStatus.BETTING},
    RoundStatus.FAILED: {RoundStatus.MANUAL_REVIEW},
    RoundStatus.MANUAL_REVIEW: {RoundStatus.SETTLING, RoundStatus.PAUSED},
}


def assert_transition(current: RoundStatus, target: RoundStatus) -> None:
    if target not in _ALLOWED[current]:
        raise ValueError(f"invalid round transition: {current.value} -> {target.value}")
