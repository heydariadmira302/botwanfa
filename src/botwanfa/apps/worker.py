import asyncio

import structlog
from sqlalchemy import select

from botwanfa.config import get_settings
from botwanfa.db.models import Round
from botwanfa.db.session import create_engine_and_session
from botwanfa.domain.state_machine import RoundStatus
from botwanfa.logging import configure_logging
from botwanfa.services.settlement import SettlementService

log = structlog.get_logger()
settlement_service = SettlementService()


async def tick(session_factory) -> int:
    async with session_factory() as session, session.begin():
        round_id = await session.scalar(
            select(Round.id)
            .where(Round.status == RoundStatus.SETTLING.value)
            .order_by(Round.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if round_id is None:
            return 0
        results = await settlement_service.settle(session, round_id)
        log.info("round_settled", round_id=round_id, players=len(results))
        return 1


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine, factory = create_engine_and_session(settings.database_url)
    try:
        while True:
            try:
                worked = await tick(factory)
            except Exception:
                log.exception("worker_tick_failed")
                worked = 0
            if not worked:
                await asyncio.sleep(0.5)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run())
