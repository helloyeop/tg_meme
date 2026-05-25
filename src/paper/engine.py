from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.settings import get_settings
from data_sources.types import TokenMarketData
from db.models import PaperPosition, PaperTradeFill, TokenCallEvent, TokenMarketSnapshot
from scoring.engine import ScoreResult


@dataclass
class PaperDecision:
    opened: bool
    reason: str
    position: PaperPosition | None = None


class PaperTradingEngine:
    def __init__(self, strategy: dict | None = None):
        self.strategy = strategy or get_settings().load_strategy_config()
        self.paper = self.strategy.get("paper", {})
        self.entry = self.strategy.get("entry", {})
        self.exit = self.strategy.get("exit", {})

    def maybe_open_position(
        self,
        session: Session,
        *,
        event: TokenCallEvent,
        score: ScoreResult,
        market_data: TokenMarketData | None,
        now: datetime | None = None,
    ) -> PaperDecision:
        now = now or datetime.utcnow()
        if market_data is None or market_data.market_cap_usd is None:
            return PaperDecision(False, "missing_market_cap")
        if score.final_signal_score < self.entry.get("final_signal_score_min", 55):
            return PaperDecision(False, "score_below_threshold")
        if score.risk_score < self.entry.get("risk_score_min", 65):
            return PaperDecision(False, "risk_below_threshold")
        if (market_data.liquidity_usd or 0) < self.entry.get("min_liquidity_usd", 5000):
            return PaperDecision(False, "liquidity_below_threshold")
        if event.warning_count and self.entry.get("no_warning_in_event", True):
            return PaperDecision(False, "event_has_warning")
        if event.sold_count and self.entry.get("no_sold_message_in_event", True):
            return PaperDecision(False, "event_has_sold")
        if self._has_open_same_event(session, event):
            return PaperDecision(False, "position_already_open")
        if self._daily_loss(session, now.date()) <= -abs(self.paper.get("daily_max_loss_sol", 0.5)):
            return PaperDecision(False, "daily_loss_limit_reached")

        entry_size_sol = self.paper.get("entry_size_sol", get_settings().paper_entry_size_sol)
        slippage = self.strategy.get("paper", {}).get("estimated_slippage_pct", 5) / 100
        entry_market_cap = market_data.market_cap_usd * (1 + slippage)
        raw_price = market_data.price_usd or 0.0
        entry_price = raw_price * (1 + slippage)
        position = PaperPosition(
            event_id=event.id,
            channel_id=event.channel_id,
            token_address=event.token_address,
            status="OPEN",
            entry_time=now,
            entry_price_usd=entry_price,
            entry_market_cap_usd=entry_market_cap,
            entry_size_sol=entry_size_sol,
            remaining_ratio=1.0,
            highest_price_usd=entry_price,
            stop_loss_price_usd=entry_price * (1 + self.exit.get("stop_loss_pct", -25) / 100),
            highest_market_cap_usd=entry_market_cap,
            stop_loss_market_cap_usd=entry_market_cap * (1 + self.exit.get("stop_loss_pct", -25) / 100),
        )
        session.add(position)
        session.flush()
        session.add(
            PaperTradeFill(
                position_id=position.id,
                fill_time=now,
                side="BUY",
                price_usd=entry_price,
                market_cap_usd=entry_market_cap,
                size_ratio=1.0,
                size_sol=entry_size_sol,
                reason="signal_entry",
            )
        )
        session.flush()
        return PaperDecision(True, "opened", position)

    def update_position(
        self,
        session: Session,
        *,
        position: PaperPosition,
        current_market_cap_usd: float,
        current_price_usd: float | None = None,
        reason: str = "price_update",
        now: datetime | None = None,
    ) -> PaperPosition:
        now = now or datetime.utcnow()
        if not position.entry_market_cap_usd:
            return position
        position.highest_market_cap_usd = max(position.highest_market_cap_usd or 0, current_market_cap_usd)
        return_pct = ((current_market_cap_usd / position.entry_market_cap_usd) - 1) * 100
        position.unrealized_pnl_sol = position.entry_size_sol * position.remaining_ratio * (return_pct / 100)

        if return_pct <= self.exit.get("stop_loss_pct", -25):
            self._sell(session, position, current_market_cap_usd, current_price_usd, 1.0, "stop_loss", now)
        elif return_pct >= self.exit.get("take_profit_2_pct", 100) and position.remaining_ratio > 0.2:
            self._sell(session, position, current_market_cap_usd, current_price_usd, self.exit.get("take_profit_2_sell_ratio", 30) / 100, "take_profit_2", now)
        elif return_pct >= self.exit.get("take_profit_1_pct", 30) and position.remaining_ratio > 0.5:
            self._sell(session, position, current_market_cap_usd, current_price_usd, self.exit.get("take_profit_1_sell_ratio", 50) / 100, "take_profit_1", now)
        elif reason in {"WARNING", "SOLD"}:
            self._sell(session, position, current_market_cap_usd, current_price_usd, 1.0, f"message_{reason.lower()}", now)

        session.flush()
        return position

    def sync_post_exit_tracking(self, session: Session, position: PaperPosition) -> bool:
        if position.status != "CLOSED" or position.exit_time is None:
            return False

        snapshots = session.scalars(
            select(TokenMarketSnapshot)
            .where(
                TokenMarketSnapshot.token_address == position.token_address,
                TokenMarketSnapshot.snapshot_time >= position.exit_time,
                TokenMarketSnapshot.market_cap_usd.is_not(None),
            )
            .order_by(TokenMarketSnapshot.snapshot_time.asc(), TokenMarketSnapshot.id.asc())
        ).all()
        if not snapshots:
            return False

        if position.post_exit_reference_market_cap_usd is None:
            position.post_exit_reference_market_cap_usd = position.entry_market_cap_usd
        if position.post_exit_reference_market_cap_usd is None:
            entry_snapshots = session.scalars(
                select(TokenMarketSnapshot)
                .where(
                    TokenMarketSnapshot.token_address == position.token_address,
                    TokenMarketSnapshot.snapshot_time >= position.entry_time - timedelta(minutes=10),
                    TokenMarketSnapshot.snapshot_time <= position.entry_time + timedelta(minutes=10),
                    TokenMarketSnapshot.market_cap_usd.is_not(None),
                )
            ).all()
            entry_snapshot = min(
                entry_snapshots,
                key=lambda snapshot: abs((snapshot.snapshot_time - position.entry_time).total_seconds()),
                default=None,
            )
            if entry_snapshot and entry_snapshot.market_cap_usd is not None:
                slippage = self.paper.get("estimated_slippage_pct", 5) / 100
                position.post_exit_reference_market_cap_usd = entry_snapshot.market_cap_usd * (1 + slippage)

        latest = snapshots[-1]
        highest = max(snapshots, key=lambda snapshot: snapshot.market_cap_usd or 0)
        lowest = min(snapshots, key=lambda snapshot: snapshot.market_cap_usd or float("inf"))
        position.post_exit_latest_market_cap_usd = latest.market_cap_usd
        position.post_exit_highest_market_cap_usd = highest.market_cap_usd
        position.post_exit_lowest_market_cap_usd = lowest.market_cap_usd
        position.post_exit_latest_snapshot_time = latest.snapshot_time
        position.post_exit_highest_time = highest.snapshot_time
        position.post_exit_lowest_time = lowest.snapshot_time
        position.post_exit_snapshot_count = len(snapshots)
        session.flush()
        return True

    def _sell(
        self,
        session: Session,
        position: PaperPosition,
        observed_market_cap_usd: float,
        observed_price_usd: float | None,
        requested_ratio: float,
        reason: str,
        now: datetime,
    ) -> None:
        slippage = self.strategy.get("paper", {}).get("estimated_slippage_pct", 5) / 100
        sell_market_cap = observed_market_cap_usd * (1 - slippage)
        sell_price = (observed_price_usd or 0.0) * (1 - slippage)
        sell_ratio = min(position.remaining_ratio, requested_ratio)
        size_sol = position.entry_size_sol * sell_ratio
        pnl_sol = size_sol * ((sell_market_cap / position.entry_market_cap_usd) - 1)
        position.remaining_ratio = max(0, position.remaining_ratio - sell_ratio)
        position.realized_pnl_sol += pnl_sol
        position.unrealized_pnl_sol = position.entry_size_sol * position.remaining_ratio * ((sell_market_cap / position.entry_market_cap_usd) - 1)
        if position.remaining_ratio <= 0.0001:
            position.status = "CLOSED"
            position.exit_time = now
            position.exit_reason = reason
            position.remaining_ratio = 0
        else:
            position.status = "PARTIALLY_CLOSED"

        session.add(
            PaperTradeFill(
                position_id=position.id,
                fill_time=now,
                side="SELL",
                price_usd=sell_price,
                market_cap_usd=sell_market_cap,
                size_ratio=sell_ratio,
                size_sol=size_sol,
                pnl_sol=pnl_sol,
                reason=reason,
            )
        )

    def _has_open_same_event(self, session: Session, event: TokenCallEvent) -> bool:
        return bool(
            session.scalar(
                select(PaperPosition.id).where(
                    PaperPosition.event_id == event.id,
                    PaperPosition.status.in_(["OPEN", "PARTIALLY_CLOSED"]),
                )
            )
        )

    def _daily_loss(self, session: Session, day: date) -> float:
        start = datetime.combine(day, datetime.min.time())
        end = datetime.combine(day, datetime.max.time())
        pnl = session.scalar(
            select(func.coalesce(func.sum(PaperTradeFill.pnl_sol), 0.0)).where(
                PaperTradeFill.fill_time >= start,
                PaperTradeFill.fill_time <= end,
                PaperTradeFill.pnl_sol < 0,
            )
        )
        return float(pnl or 0)
