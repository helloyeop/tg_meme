from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.settings import get_settings
from data_sources.types import TokenMarketData
from db.models import LiveOrder, LivePosition, TokenCallEvent


@dataclass
class LiveDecision:
    staged: bool
    reason: str
    order: LiveOrder | None = None
    position: LivePosition | None = None


class LiveTradingEngine:
    """Stages live order intents while keeping transaction submission disabled."""

    def __init__(self, strategy: dict | None = None, settings=None):
        self.settings = settings or get_settings()
        self.strategy = strategy or self.settings.load_strategy_config()
        self.live = self.strategy.get("live", {})

    def maybe_stage_entry(
        self,
        session: Session,
        *,
        event: TokenCallEvent,
        market_data: TokenMarketData | None,
        paper_opened: bool,
        now: datetime | None = None,
    ) -> LiveDecision:
        now = now or datetime.utcnow()
        if not self.settings.live_order_staging_enabled:
            return LiveDecision(False, "live_order_staging_disabled")
        if not paper_opened:
            return LiveDecision(False, "paper_entry_not_opened")
        if market_data is None or market_data.market_cap_usd is None:
            return LiveDecision(False, "missing_market_cap")
        if not self.settings.live_wallet_public_key:
            return LiveDecision(False, "missing_live_wallet_public_key")
        if self._has_active_position(session, event.id):
            return LiveDecision(False, "live_position_already_active")
        if self._active_position_count(session) >= self.live.get("max_open_positions", 1):
            return LiveDecision(False, "live_max_open_positions_reached")

        entry_size_sol = self.live.get("entry_size_sol", 0.05)
        max_entry_size_sol = self.live.get("max_entry_size_sol", 0.05)
        if entry_size_sol > max_entry_size_sol:
            return LiveDecision(False, "live_entry_size_exceeds_cap")

        take_profit_pct = self.live.get("take_profit_pct", 10)
        target_market_cap = market_data.market_cap_usd * (1 + take_profit_pct / 100)
        position = LivePosition(
            event_id=event.id,
            channel_id=event.channel_id,
            token_address=event.token_address,
            status="ENTRY_REQUESTED",
            entry_time=now,
            entry_market_cap_usd=market_data.market_cap_usd,
            entry_size_sol=entry_size_sol,
            target_profit_pct=take_profit_pct,
            target_market_cap_usd=target_market_cap,
            highest_market_cap_usd=market_data.market_cap_usd,
        )
        session.add(position)
        session.flush()
        order = LiveOrder(
            event_id=event.id,
            position_id=position.id,
            channel_id=event.channel_id,
            token_address=event.token_address,
            side="BUY",
            status="STAGED",
            reason="signal_entry",
            requested_at=now,
            requested_size_sol=entry_size_sol,
            reference_market_cap_usd=market_data.market_cap_usd,
            target_market_cap_usd=target_market_cap,
        )
        session.add(order)
        session.flush()
        return LiveDecision(True, "entry_staged", order, position)

    def evaluate_take_profit(
        self,
        session: Session,
        *,
        position: LivePosition,
        current_market_cap_usd: float,
        now: datetime | None = None,
    ) -> LiveDecision:
        now = now or datetime.utcnow()
        position.highest_market_cap_usd = max(
            position.highest_market_cap_usd, current_market_cap_usd
        )
        if position.status != "OPEN":
            return LiveDecision(False, "live_position_not_open", position=position)
        if current_market_cap_usd < position.target_market_cap_usd:
            return LiveDecision(False, "take_profit_not_reached", position=position)
        if self._has_pending_exit(session, position.id):
            return LiveDecision(False, "live_exit_already_staged", position=position)

        position.status = "EXIT_REQUESTED"
        position.exit_requested_time = now
        order = LiveOrder(
            event_id=position.event_id,
            position_id=position.id,
            channel_id=position.channel_id,
            token_address=position.token_address,
            side="SELL",
            status="STAGED",
            reason="take_profit_10_pct",
            requested_at=now,
            reference_market_cap_usd=current_market_cap_usd,
            target_market_cap_usd=position.target_market_cap_usd,
        )
        session.add(order)
        session.flush()
        return LiveDecision(True, "take_profit_exit_staged", order, position)

    def _has_active_position(self, session: Session, event_id: int) -> bool:
        return bool(
            session.scalar(
                select(LivePosition.id).where(
                    LivePosition.event_id == event_id,
                    LivePosition.status.in_(["ENTRY_REQUESTED", "OPEN", "EXIT_REQUESTED"]),
                )
            )
        )

    def _active_position_count(self, session: Session) -> int:
        return int(
            session.scalar(
                select(func.count(LivePosition.id)).where(
                    LivePosition.status.in_(["ENTRY_REQUESTED", "OPEN", "EXIT_REQUESTED"])
                )
            )
            or 0
        )

    def _has_pending_exit(self, session: Session, position_id: int) -> bool:
        return bool(
            session.scalar(
                select(LiveOrder.id).where(
                    LiveOrder.position_id == position_id,
                    LiveOrder.side == "SELL",
                    LiveOrder.status.in_(["STAGED", "SIGNED", "SUBMITTED"]),
                )
            )
        )
