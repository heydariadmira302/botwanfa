from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from botwanfa.db.models import (
    GameSettings,
    GroupMember,
    OddsSetting,
    TelegramGroup,
    User,
    Wallet,
)
from botwanfa.domain.bets import BetType

DEFAULT_STARTING_BALANCE = Decimal("0.00")

DEFAULT_ODDS = {
    BetType.BIG: Decimal("2.00"),
    BetType.SMALL: Decimal("2.00"),
    BetType.ODD: Decimal("2.00"),
    BetType.EVEN: Decimal("2.00"),
    BetType.BIG_ODD: Decimal("4.00"),
    BetType.BIG_EVEN: Decimal("4.00"),
    BetType.SMALL_ODD: Decimal("4.00"),
    BetType.SMALL_EVEN: Decimal("4.00"),
    BetType.STRAIGHT: Decimal("9.00"),
    BetType.ANY_TRIPLE: Decimal("25.00"),
    BetType.SPECIFIC_TRIPLE: Decimal("151.00"),
}


async def provision_participant(
    session: AsyncSession,
    *,
    group_id: int,
    group_title: str,
    user_id: int,
    username: str | None,
    display_name: str,
) -> None:
    await session.execute(
        insert(User)
        .values(id=user_id, username=username, display_name=display_name)
        .on_conflict_do_update(
            index_elements=[User.id],
            set_={"username": username, "display_name": display_name},
        )
    )
    await session.execute(
        insert(TelegramGroup)
        .values(id=group_id, title=group_title)
        .on_conflict_do_update(index_elements=[TelegramGroup.id], set_={"title": group_title})
    )
    await session.execute(
        insert(GroupMember)
        .values(group_id=group_id, user_id=user_id)
        .on_conflict_do_nothing(index_elements=["group_id", "user_id"])
    )
    await session.execute(
        insert(Wallet)
        .values(
            group_id=group_id,
            user_id=user_id,
            balance=DEFAULT_STARTING_BALANCE,
        )
        .on_conflict_do_nothing(index_elements=["group_id", "user_id"])
    )
    wallet = await session.scalar(
        select(Wallet).where(Wallet.group_id == group_id, Wallet.user_id == user_id)
    )
    if wallet is None:
        raise RuntimeError("wallet provisioning failed")
    await session.execute(
        insert(GameSettings)
        .values(group_id=group_id)
        .on_conflict_do_nothing(index_elements=[GameSettings.group_id])
    )
    for bet_type, multiplier in DEFAULT_ODDS.items():
        values = [""]
        if bet_type == BetType.SUM:
            values = [str(value) for value in range(3, 19)]
        if bet_type == BetType.SPECIFIC_TRIPLE:
            values = [str(value) * 3 for value in range(1, 7)]
        for bet_value in values:
            await session.execute(
                insert(OddsSetting)
                .values(
                    group_id=group_id,
                    bet_type=bet_type.value,
                    bet_value=bet_value,
                    payout_multiplier=multiplier,
                )
                .on_conflict_do_nothing(index_elements=["group_id", "bet_type", "bet_value"])
            )
    for total in range(3, 19):
        await session.execute(
            insert(OddsSetting)
            .values(
                group_id=group_id,
                bet_type=BetType.SUM.value,
                bet_value=str(total),
                payout_multiplier=Decimal("6.00"),
            )
            .on_conflict_do_nothing(index_elements=["group_id", "bet_type", "bet_value"])
        )
