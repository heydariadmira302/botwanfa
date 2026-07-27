from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from botwanfa.db.models import (
    Bet,
    DiceResult,
    Round,
    RoundPlayerSettlement,
    StreakReward,
    TelegramGroup,
    User,
)


@dataclass(frozen=True, slots=True)
class BetAuditItem:
    bet_type: str
    bet_value: str
    amount: Decimal
    odds: Decimal
    won: bool | None
    payout: Decimal


@dataclass(frozen=True, slots=True)
class PlayerRoundAudit:
    user_id: int
    display_name: str
    items: tuple[BetAuditItem, ...]
    wagered: Decimal
    returned: Decimal | None
    net: Decimal | None
    balance_after: Decimal | None
    streak_reward: Decimal


@dataclass(frozen=True, slots=True)
class RoundAuditReport:
    public_code: str
    group_id: int
    group_title: str
    status: str
    dice: tuple[int, int, int] | None
    dice_source: str | None
    players: tuple[PlayerRoundAudit, ...]

    @property
    def total_wagered(self) -> Decimal:
        return sum((player.wagered for player in self.players), Decimal("0.00"))

    @property
    def total_returned(self) -> Decimal | None:
        if any(player.returned is None for player in self.players):
            return None
        return sum(
            (player.returned or Decimal("0.00") for player in self.players),
            Decimal("0.00"),
        )

    @property
    def total_net(self) -> Decimal | None:
        if any(player.net is None for player in self.players):
            return None
        return sum(
            (player.net or Decimal("0.00") for player in self.players),
            Decimal("0.00"),
        )


async def load_round_audit(
    session: AsyncSession, public_code: str
) -> RoundAuditReport | None:
    round_row = (
        await session.execute(
            select(Round, TelegramGroup)
            .join(TelegramGroup, TelegramGroup.id == Round.group_id)
            .where(Round.public_code == public_code)
        )
    ).first()
    if round_row is None:
        return None
    round_, group = round_row
    dice = await session.get(DiceResult, round_.id)
    bet_rows = (
        await session.execute(
            select(Bet, User)
            .join(User, User.id == Bet.user_id)
            .where(Bet.round_id == round_.id)
            .order_by(Bet.id)
        )
    ).all()
    settlements = {
        row.user_id: row
        for row in (
            await session.scalars(
                select(RoundPlayerSettlement).where(
                    RoundPlayerSettlement.round_id == round_.id
                )
            )
        ).all()
    }
    rewards = {
        int(user_id): Decimal(reward)
        for user_id, reward in (
            await session.execute(
                select(
                    StreakReward.user_id,
                    func.coalesce(func.sum(StreakReward.reward), 0),
                )
                .where(StreakReward.round_id == round_.id)
                .group_by(StreakReward.user_id)
            )
        ).all()
    }
    grouped: dict[int, tuple[User, list[BetAuditItem]]] = {}
    for bet, user in bet_rows:
        if user.id not in grouped:
            grouped[user.id] = (user, [])
        grouped[user.id][1].append(
            BetAuditItem(
                bet_type=bet.bet_type,
                bet_value=bet.bet_value or "",
                amount=bet.amount,
                odds=bet.odds_snapshot,
                won=bet.won,
                payout=bet.payout,
            )
        )
    players = []
    for user_id, (user, items) in grouped.items():
        settlement = settlements.get(user_id)
        wagered = sum((item.amount for item in items), Decimal("0.00"))
        players.append(
            PlayerRoundAudit(
                user_id=user_id,
                display_name=user.display_name,
                items=tuple(items),
                wagered=settlement.wagered if settlement else wagered,
                returned=settlement.returned if settlement else None,
                net=settlement.net if settlement else None,
                balance_after=settlement.balance_after if settlement else None,
                streak_reward=rewards.get(user_id, Decimal("0.00")),
            )
        )
    return RoundAuditReport(
        public_code=round_.public_code,
        group_id=round_.group_id,
        group_title=group.title,
        status=round_.status,
        dice=(dice.die_1, dice.die_2, dice.die_3) if dice else None,
        dice_source=dice.source if dice else None,
        players=tuple(players),
    )
