from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botwanfa.db.models import (
    Bet,
    DiceResult,
    GameSettings,
    OutboxMessage,
    Round,
    RoundPlayerSettlement,
    StreakReward,
    Wallet,
    WalletLedger,
    WinningStreak,
)
from botwanfa.domain.bets import BetItem, BetType
from botwanfa.domain.dice import evaluate_dice, is_winning_bet
from botwanfa.domain.state_machine import RoundStatus


class SettlementService:
    async def settle(self, session: AsyncSession, round_id: int) -> list[RoundPlayerSettlement]:
        round_ = await session.scalar(select(Round).where(Round.id == round_id).with_for_update())
        if round_ is None:
            raise ValueError("期次不存在")
        existing = (
            await session.scalars(
                select(RoundPlayerSettlement).where(RoundPlayerSettlement.round_id == round_id)
            )
        ).all()
        if round_.status == RoundStatus.COMPLETED.value:
            return list(existing)
        if round_.status != RoundStatus.SETTLING.value:
            raise ValueError("期次尚未进入结算状态")

        dice = await session.get(DiceResult, round_id)
        if dice is None:
            raise ValueError("缺少骰子结果")
        outcome = evaluate_dice((dice.die_1, dice.die_2, dice.die_3))
        bets = (await session.scalars(select(Bet).where(Bet.round_id == round_id))).all()
        settings = await session.get(GameSettings, round_.group_id)
        by_user: dict[int, list[Bet]] = defaultdict(list)
        for bet in bets:
            by_user[bet.user_id].append(bet)

        settlements = []
        for user_id, user_bets in by_user.items():
            wallet = await session.scalar(
                select(Wallet)
                .where(Wallet.group_id == round_.group_id, Wallet.user_id == user_id)
                .with_for_update()
            )
            if wallet is None:
                raise ValueError("结算钱包不存在")
            wagered = sum((bet.amount for bet in user_bets), Decimal("0.00"))
            returned = Decimal("0.00")
            for bet in user_bets:
                item = BetItem(BetType(bet.bet_type), bet.amount, bet.bet_value or None)
                bet.won = is_winning_bet(item, outcome)
                bet.payout = bet.amount * bet.odds_snapshot if bet.won else Decimal("0.00")
                returned += bet.payout
            wallet.balance += returned
            streak = await session.scalar(
                select(WinningStreak)
                .where(
                    WinningStreak.group_id == round_.group_id,
                    WinningStreak.user_id == user_id,
                )
                .with_for_update()
            )
            if streak is None:
                streak = WinningStreak(
                    group_id=round_.group_id,
                    user_id=user_id,
                    current_count=0,
                    highest_count=0,
                )
                session.add(streak)
            net = returned - wagered
            if net > 0:
                streak.current_count += 1
                streak.highest_count = max(streak.highest_count, streak.current_count)
            elif net < 0:
                streak.current_count = 0
            streak_reward = Decimal("0.00")
            if settings and settings.streak_enabled and net > 0:
                streak_reward = Decimal(
                    settings.streak_rewards.get(str(streak.current_count), "0.00")
                )
            if streak_reward > 0:
                wallet.balance += streak_reward
                session.add(
                    StreakReward(
                        round_id=round_id,
                        group_id=round_.group_id,
                        user_id=user_id,
                        streak_count=streak.current_count,
                        reward=streak_reward,
                    )
                )
                session.add(
                    WalletLedger(
                        wallet_id=wallet.id,
                        idempotency_key=f"streak:{round_id}:{user_id}:{streak.current_count}",
                        entry_type="streak_reward",
                        amount=streak_reward,
                        balance_after=wallet.balance,
                        reference_type="round",
                        reference_id=round_id,
                        note=f"{streak.current_count}连胜奖励",
                    )
                )
            settlement = RoundPlayerSettlement(
                round_id=round_id,
                group_id=round_.group_id,
                user_id=user_id,
                wagered=wagered,
                returned=returned,
                net=net,
                balance_after=wallet.balance,
            )
            session.add(settlement)
            session.add(
                WalletLedger(
                    wallet_id=wallet.id,
                    idempotency_key=f"settlement:{round_id}:{user_id}",
                    entry_type="settlement_credit",
                    amount=returned,
                    balance_after=wallet.balance,
                    reference_type="round",
                    reference_id=round_id,
                )
            )
            settlements.append(settlement)
        round_.status = RoundStatus.COMPLETED.value
        round_.completed_at = datetime.now(UTC)
        available_at = datetime.now(UTC) + timedelta(seconds=4)
        session.add(
            OutboxMessage(
                group_id=round_.group_id,
                sequence=40,
                message_type="trend_result",
                payload={"round_id": round_id, "round_number": round_.round_number},
                idempotency_key=f"round:{round_id}:trend-result",
                available_at=available_at,
            )
        )
        session.add(
            OutboxMessage(
                group_id=round_.group_id,
                sequence=50,
                message_type="settlement_summary",
                payload={"round_id": round_id, "round_number": round_.round_number},
                idempotency_key=f"round:{round_id}:settlement-summary",
                available_at=available_at,
            )
        )
        await session.flush()
        return settlements
