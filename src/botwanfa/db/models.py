from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from botwanfa.db.base import Base, utcnow

MONEY = Numeric(20, 2)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TelegramGroup(Base):
    __tablename__ = "telegram_groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("telegram_groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id"),
        CheckConstraint("balance >= 0", name="nonnegative_balance"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("telegram_groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    balance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    ledger_entries: Mapped[list[WalletLedger]] = relationship(back_populates="wallet")


class WalletLedger(Base):
    __tablename__ = "wallet_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    entry_type: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    balance_after: Mapped[Decimal] = mapped_column(MONEY)
    reference_type: Mapped[str | None] = mapped_column(String(32))
    reference_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    wallet: Mapped[Wallet] = relationship(back_populates="ledger_entries")


class GameSettings(Base):
    __tablename__ = "game_settings"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_groups.id", ondelete="CASCADE"), primary_key=True
    )
    betting_seconds: Mapped[int] = mapped_column(Integer, default=30)
    rolling_seconds: Mapped[int] = mapped_column(Integer, default=10)
    next_round_seconds: Mapped[int] = mapped_column(Integer, default=15)
    player_dice_seconds: Mapped[int] = mapped_column(
        Integer, default=25, server_default="25"
    )
    minimum_bet: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("1.00"))
    player_dice_threshold: Mapped[Decimal | None] = mapped_column(
        MONEY, default=Decimal("0.01"), server_default="0.01"
    )
    history_size: Mapped[int] = mapped_column(Integer, default=84)
    checkin_min: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.10"))
    checkin_max: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.50"))
    checkin_step: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.10"))
    streak_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    streak_rewards: Mapped[dict[str, str]] = mapped_column(
        JSON, default=lambda: {"3": "10.00", "5": "30.00", "10": "100.00"}
    )
    test_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    message_buttons: Mapped[dict[str, list[dict[str, str]]]] = mapped_column(
        JSON, default=dict, server_default=text("'{}'::json")
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    effective_from_round: Mapped[int | None] = mapped_column(BigInteger)


class OddsSetting(Base):
    __tablename__ = "odds_settings"
    __table_args__ = (
        UniqueConstraint("group_id", "bet_type", "bet_value"),
        CheckConstraint("payout_multiplier > 0", name="positive_multiplier"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("telegram_groups.id", ondelete="CASCADE"))
    bet_type: Mapped[str] = mapped_column(String(32))
    bet_value: Mapped[str] = mapped_column(String(16), default="")
    payout_multiplier: Mapped[Decimal] = mapped_column(MONEY)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (
        UniqueConstraint("group_id", "round_number"),
        Index("uq_rounds_public_code", "public_code", unique=True),
        Index(
            "uq_rounds_one_active_per_group",
            "group_id",
            unique=True,
            postgresql_where=text("status <> 'completed'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_groups.id", ondelete="CASCADE"), index=True
    )
    round_number: Mapped[int] = mapped_column(BigInteger)
    public_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    betting_opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    betting_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiceResult(Base):
    __tablename__ = "dice_results"

    round_id: Mapped[int] = mapped_column(
        ForeignKey("rounds.id", ondelete="CASCADE"), primary_key=True
    )
    die_1: Mapped[int] = mapped_column(Integer)
    die_2: Mapped[int] = mapped_column(Integer)
    die_3: Mapped[int] = mapped_column(Integer)
    telegram_message_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(32), default="bot")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BetBatch(Base):
    __tablename__ = "bet_batches"
    __table_args__ = (UniqueConstraint("group_id", "telegram_message_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("telegram_groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    original_text: Mapped[str] = mapped_column(Text)
    total_amount: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("bet_batches.id", ondelete="CASCADE"), index=True
    )
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("telegram_groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    bet_type: Mapped[str] = mapped_column(String(32))
    bet_value: Mapped[str] = mapped_column(String(16), default="")
    amount: Mapped[Decimal] = mapped_column(MONEY)
    odds_snapshot: Mapped[Decimal] = mapped_column(MONEY)
    won: Mapped[bool | None] = mapped_column(Boolean)
    payout: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))


class RoundPlayerSettlement(Base):
    __tablename__ = "round_player_settlements"
    __table_args__ = (UniqueConstraint("round_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(ForeignKey("telegram_groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    wagered: Mapped[Decimal] = mapped_column(MONEY)
    returned: Mapped[Decimal] = mapped_column(MONEY)
    net: Mapped[Decimal] = mapped_column(MONEY)
    balance_after: Mapped[Decimal] = mapped_column(MONEY)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyTurnover(Base):
    __tablename__ = "daily_turnover"
    __table_args__ = (UniqueConstraint("group_id", "user_id", "business_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("telegram_groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    business_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))


class DailyCheckin(Base):
    __tablename__ = "daily_checkins"
    __table_args__ = (UniqueConstraint("group_id", "user_id", "business_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("telegram_groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    business_date: Mapped[date] = mapped_column(Date)
    reward: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    message_type: Mapped[str] = mapped_column(String(32), default="text")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class DeploymentControl(Base):
    __tablename__ = "deployment_control"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    draining: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[int | None] = mapped_column(BigInteger)
    outbox_start_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    ready_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WinningStreak(Base):
    __tablename__ = "winning_streaks"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    current_count: Mapped[int] = mapped_column(Integer, default=0)
    highest_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StreakReward(Base):
    __tablename__ = "streak_rewards"
    __table_args__ = (UniqueConstraint("round_id", "user_id", "streak_count"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(BigInteger)
    group_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    streak_count: Mapped[int] = mapped_column(Integer)
    reward: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(30), default="super_admin")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    group_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(500), unique=True)
    backup_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
