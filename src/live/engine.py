from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.settings import get_settings
from data_sources.types import TokenMarketData
from db.models import LiveOrder, LivePosition, TokenCallEvent

LAMPORTS_PER_SOL = 1_000_000_000


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
        round_trip_recovery_pct: float | None = None,
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

        entry_size_sol = self._entry_size_sol(event)
        max_entry_size_sol = self.live.get("max_entry_size_sol", 0.05)
        if entry_size_sol > max_entry_size_sol:
            return LiveDecision(False, "live_entry_size_exceeds_cap")
        if self._daily_realized_loss_sol(session, now) >= self.live.get("daily_max_loss_sol", 1):
            return LiveDecision(False, "live_daily_loss_limit_reached")
        if self.live.get("require_entry_round_trip_quote", False):
            if round_trip_recovery_pct is None:
                return LiveDecision(False, "live_entry_round_trip_quote_unavailable")
            if round_trip_recovery_pct < self.live.get(
                "min_entry_round_trip_recovery_pct", 90
            ):
                return LiveDecision(False, "live_entry_round_trip_recovery_too_low")

        take_profit_pct = self._take_profit_pct(market_data.market_cap_usd)
        stop_loss_pct = self.live.get("stop_loss_pct", -70)
        target_market_cap = market_data.market_cap_usd * (1 + take_profit_pct / 100)
        stop_loss_market_cap = market_data.market_cap_usd * (1 + stop_loss_pct / 100)
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
            stop_loss_pct=stop_loss_pct,
            stop_loss_market_cap_usd=stop_loss_market_cap,
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

    def evaluate_exit(
        self,
        session: Session,
        *,
        position: LivePosition,
        current_market_cap_usd: float,
        quoted_output_lamports: int | None = None,
        now: datetime | None = None,
    ) -> LiveDecision:
        now = now or datetime.utcnow()
        position.highest_market_cap_usd = max(
            position.highest_market_cap_usd, current_market_cap_usd
        )
        if position.status != "OPEN":
            return LiveDecision(False, "live_position_not_open", position=position)
        if self._has_pending_exit(session, position.id):
            return LiveDecision(False, "live_exit_already_staged", position=position)
        quoted_return_pct = self._quoted_return_pct(position, quoted_output_lamports)
        if quoted_return_pct is not None and quoted_return_pct >= position.target_profit_pct:
            if self._has_recent_failed_take_profit(session, position.id, now):
                return LiveDecision(False, "live_take_profit_retry_cooldown", position=position)
            reason = f"take_profit_{position.target_profit_pct:g}_pct"
        elif quoted_return_pct is not None and quoted_return_pct <= self.live.get(
            "executable_stop_loss_pct", -20
        ):
            executable_stop_loss_pct = abs(self.live.get("executable_stop_loss_pct", -20))
            reason = f"executable_stop_loss_{executable_stop_loss_pct:g}_pct"
        elif current_market_cap_usd <= position.stop_loss_market_cap_usd:
            reason = "emergency_stop_loss_70_pct"
        elif current_market_cap_usd >= position.target_market_cap_usd:
            return LiveDecision(False, "live_take_profit_quote_below_target", position=position)
        else:
            return LiveDecision(False, "live_exit_threshold_not_reached", position=position)

        position.status = "EXIT_REQUESTED"
        position.exit_requested_time = now
        order = LiveOrder(
            event_id=position.event_id,
            position_id=position.id,
            channel_id=position.channel_id,
            token_address=position.token_address,
            side="SELL",
            status="STAGED",
            reason=reason,
            requested_at=now,
            reference_market_cap_usd=current_market_cap_usd,
            target_market_cap_usd=position.target_market_cap_usd,
        )
        session.add(order)
        session.flush()
        return LiveDecision(True, f"{reason}_exit_staged", order, position)

    def _daily_realized_loss_sol(self, session: Session, now: datetime) -> float:
        day_start = datetime.combine(now.date(), time.min)
        realized = session.scalar(
            select(func.sum(LivePosition.realized_pnl_sol)).where(
                LivePosition.status == "CLOSED",
                LivePosition.exit_confirmed_time >= day_start,
                LivePosition.realized_pnl_sol < 0,
            )
        )
        return abs(float(realized or 0))

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

    def _has_recent_failed_take_profit(
        self, session: Session, position_id: int, now: datetime
    ) -> bool:
        retry_seconds = self.live.get("take_profit_retry_seconds", 30)
        cutoff = now - timedelta(seconds=retry_seconds)
        return bool(
            session.scalar(
                select(LiveOrder.id).where(
                    LiveOrder.position_id == position_id,
                    LiveOrder.side == "SELL",
                    LiveOrder.reason.startswith("take_profit"),
                    LiveOrder.status == "FAILED",
                    LiveOrder.requested_at >= cutoff,
                )
            )
        )

    def _entry_size_sol(self, event: TokenCallEvent) -> float:
        size = self.live.get("entry_size_sol", 0.05)
        if (event.actionable_signal_count or 0) > 1:
            factor = self.strategy.get("actionable_recall", {}).get("entry_size_factor", 0.5)
            return size * factor
        return size

    def _take_profit_pct(self, entry_market_cap_usd: float) -> float:
        default = self.live.get("take_profit_pct", 10)
        tiers = self.live.get("take_profit_by_entry_market_cap", {})
        if entry_market_cap_usd < 500_000:
            return tiers.get("below_500k_pct", default)
        if entry_market_cap_usd < 1_000_000:
            return tiers.get("from_500k_to_below_1m_pct", default)
        return tiers.get("at_or_above_1m_pct", default)

    def _quoted_return_pct(
        self,
        position: LivePosition,
        quoted_output_lamports: int | None,
    ) -> float | None:
        if quoted_output_lamports is None:
            return None
        entry_input_lamports = int(
            position.entry_input_lamports or position.entry_size_sol * LAMPORTS_PER_SOL
        )
        return 100 * (quoted_output_lamports / entry_input_lamports - 1)
