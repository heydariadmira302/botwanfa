from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botwanfa.db.models import Bet, BetBatch, OddsSetting, Round, Wallet, WalletLedger
from botwanfa.domain.bets import BetItem
from botwanfa.domain.state_machine import RoundStatus


class BettingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlaceBetResult:
    batch_id: int
    total_amount: Decimal
    balance_after: Decimal
    duplicate: bool = False


class BettingService:
    async def place_batch(
        self,
        session: AsyncSession,
        *,
        group_id: int,
        user_id: int,
        telegram_message_id: int,
        original_text: str,
        items: list[BetItem],
        now: datetime | None = None,
    ) -> PlaceBetResult:
        now = now or datetime.now(UTC)
        existing = await session.scalar(
            select(BetBatch).where(
                BetBatch.group_id == group_id,
                BetBatch.telegram_message_id == telegram_message_id,
            )
        )
        if existing:
            wallet = await self._locked_wallet(session, group_id, user_id)
            return PlaceBetResult(existing.id, existing.total_amount, wallet.balance, True)

        round_ = await session.scalar(
            select(Round)
            .where(Round.group_id == group_id, Round.status == RoundStatus.BETTING.value)
            .with_for_update()
        )
        if not round_ or (round_.betting_closes_at and now >= round_.betting_closes_at):
            raise BettingError("当前不在下注时间")
        existing = await session.scalar(
            select(BetBatch).where(
                BetBatch.group_id == group_id,
                BetBatch.telegram_message_id == telegram_message_id,
            )
        )
        if existing:
            wallet = await self._locked_wallet(session, group_id, user_id)
            return PlaceBetResult(existing.id, existing.total_amount, wallet.balance, True)
        if not items:
            raise BettingError("下注内容为空")

        minimum = Decimal(str(round_.settings_snapshot.get("minimum_bet", "1.00")))
        if any(item.amount < minimum for item in items):
            raise BettingError(f"单项下注最低为 {minimum}")

        odds_rows = (
            await session.scalars(
                select(OddsSetting).where(
                    OddsSetting.group_id == group_id,
                    OddsSetting.enabled.is_(True),
                )
            )
        ).all()
        odds = {(row.bet_type, row.bet_value): row.payout_multiplier for row in odds_rows}
        snapshots: list[Decimal] = []
        for item in items:
            key = (item.bet_type.value, item.value or "")
            multiplier = odds.get(key) or odds.get((item.bet_type.value, ""))
            if multiplier is None:
                raise BettingError(f"玩法未启用或缺少倍率：{item.source}")
            snapshots.append(multiplier)

        total = sum((item.amount for item in items), Decimal("0.00"))
        wallet = await self._locked_wallet(session, group_id, user_id)
        if wallet.balance < total:
            raise BettingError(f"余额不足，当前余额 {wallet.balance}")

        wallet.balance -= total
        batch = BetBatch(
            round_id=round_.id,
            group_id=group_id,
            user_id=user_id,
            telegram_message_id=telegram_message_id,
            original_text=original_text,
            total_amount=total,
        )
        session.add(batch)
        await session.flush()
        session.add_all(
            Bet(
                batch_id=batch.id,
                round_id=round_.id,
                group_id=group_id,
                user_id=user_id,
                bet_type=item.bet_type.value,
                bet_value=item.value or "",
                amount=item.amount,
                odds_snapshot=multiplier,
            )
            for item, multiplier in zip(items, snapshots, strict=True)
        )
        session.add(
            WalletLedger(
                wallet_id=wallet.id,
                idempotency_key=f"bet:{group_id}:{telegram_message_id}",
                entry_type="bet_debit",
                amount=-total,
                balance_after=wallet.balance,
                reference_type="bet_batch",
                reference_id=batch.id,
            )
        )
        await session.flush()
        return PlaceBetResult(batch.id, total, wallet.balance)

    async def _locked_wallet(self, session: AsyncSession, group_id: int, user_id: int) -> Wallet:
        wallet = await session.scalar(
            select(Wallet)
            .where(Wallet.group_id == group_id, Wallet.user_id == user_id)
            .with_for_update()
        )
        if wallet is None:
            raise BettingError("玩家钱包尚未建立")
        return wallet
