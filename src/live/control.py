from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from alerts.bot import format_token_label, format_usd, short_token_address
from app.settings import get_settings
from data_sources.aggregator import DataSourceAggregator
from db.models import (
    LiveControlState,
    LiveOrder,
    LivePosition,
    ManualLiveTrigger,
    TokenCallEvent,
    TokenMarketSnapshot,
)
from db.repositories import store_market_snapshot

ENTRY_PAUSED_KEY = "live_entry_paused"
ACTIVE_POSITION_STATUSES = ("ENTRY_REQUESTED", "OPEN", "EXIT_REQUESTED")
PENDING_ORDER_STATUSES = ("STAGED", "SIGNED", "SUBMITTED")
MANUAL_LIVE_CHANNEL_PREFIX = "manual_live_control"


@dataclass
class ControlResult:
    ok: bool
    message: str


def live_entry_paused(session: Session) -> bool:
    row = session.get(LiveControlState, ENTRY_PAUSED_KEY)
    return (row.value.lower() in {"1", "true", "yes", "paused"}) if row else False


def set_live_entry_paused(session: Session, paused: bool, note: str | None = None) -> None:
    row = session.get(LiveControlState, ENTRY_PAUSED_KEY)
    value = "true" if paused else "false"
    if row is None:
        session.add(LiveControlState(key=ENTRY_PAUSED_KEY, value=value, note=note))
    else:
        row.value = value
        row.note = note


def list_live_positions(session: Session) -> str:
    positions = session.scalars(
        select(LivePosition)
        .where(LivePosition.status.in_(ACTIVE_POSITION_STATUSES))
        .order_by(LivePosition.entry_time.desc(), LivePosition.id.desc())
    ).all()
    paused = live_entry_paused(session)
    if not positions:
        return f"Live positions: none\nNew entries: {'paused' if paused else 'enabled'}"

    lines = [
        f"Live positions: {len(positions)}",
        f"New entries: {'paused' if paused else 'enabled'}",
    ]
    for position in positions:
        lines.extend(["", format_position_summary(session, position)])
    return "\n".join(lines)


def get_position_detail(session: Session, position_id: int) -> ControlResult:
    position = session.get(LivePosition, position_id)
    if position is None:
        return ControlResult(False, f"Position #{position_id} not found.")
    return ControlResult(True, format_position_summary(session, position, include_details=True))


def stage_manual_sell(session: Session, position_id: int) -> ControlResult:
    position = session.get(LivePosition, position_id)
    if position is None:
        return ControlResult(False, f"Position #{position_id} not found.")
    if position.status != "OPEN":
        return ControlResult(False, f"Position #{position_id} is {position.status}, not OPEN.")
    if not position.token_amount_raw:
        return ControlResult(False, f"Position #{position_id} has no confirmed token amount.")
    if _has_pending_sell(session, position_id):
        return ControlResult(False, f"Position #{position_id} already has a pending SELL order.")

    now = datetime.utcnow()
    position.status = "EXIT_REQUESTED"
    position.exit_requested_time = now
    order = LiveOrder(
        event_id=position.event_id,
        position_id=position.id,
        channel_id=position.channel_id,
        token_address=position.token_address,
        side="SELL",
        status="STAGED",
        reason="manual_sell",
        requested_at=now,
        reference_market_cap_usd=position.highest_market_cap_usd,
        target_market_cap_usd=position.target_market_cap_usd,
    )
    session.add(order)
    session.flush()
    label = _token_label(session, position.token_address)
    return ControlResult(
        True,
        "\n".join(
            [
                "Manual SELL staged",
                f"Position: #{position.id}",
                f"Order: #{order.id}",
                f"Token: {label}",
                f"CA: {short_token_address(position.token_address)}",
            ]
        ),
    )


def stage_manual_buy(
    session: Session,
    token_address: str,
    *,
    data_sources: DataSourceAggregator | None = None,
    live=None,
    entry_size_sol: float | None = None,
    now: datetime | None = None,
) -> ControlResult:
    from live.engine import LiveTradingEngine

    now = now or datetime.utcnow()
    if _has_active_token_position(session, token_address):
        return ControlResult(
            False,
            f"{short_token_address(token_address)} already has an active live position.",
        )

    data_sources = data_sources or DataSourceAggregator()
    market_data = data_sources.get_market_data(token_address)
    if market_data is None or market_data.market_cap_usd is None:
        return ControlResult(
            False,
            f"Market cap unavailable for {short_token_address(token_address)}.",
        )
    store_market_snapshot(session, market_data)

    event = TokenCallEvent(
        channel_id=f"{MANUAL_LIVE_CHANNEL_PREFIX}:{now.strftime('%Y%m%d%H%M%S%f')}",
        token_address=token_address,
        first_seen_time=now,
        first_actionable_call_time=now,
        latest_actionable_call_time=now,
        first_seen_price_usd=market_data.price_usd,
        first_seen_fdv_usd=market_data.fdv_usd,
        first_seen_market_cap_usd=market_data.market_cap_usd,
        first_actionable_market_cap_usd=market_data.market_cap_usd,
        latest_actionable_market_cap_usd=market_data.market_cap_usd,
        first_seen_liquidity_usd=market_data.liquidity_usd,
        latest_price_usd=market_data.price_usd,
        latest_fdv_usd=market_data.fdv_usd,
        latest_market_cap_usd=market_data.market_cap_usd,
        latest_liquidity_usd=market_data.liquidity_usd,
        last_update_time=now,
        call_count=1,
        actionable_signal_count=1,
    )
    session.add(event)
    session.flush()

    live = live or LiveTradingEngine()
    try:
        recovery_pct = _manual_entry_round_trip_recovery_pct(
            live,
            token_address,
            event,
            entry_size_sol=entry_size_sol,
        )
    except Exception as exc:
        return ControlResult(False, f"Manual BUY rejected: round_trip_quote_failed:{exc}")
    decision = live.maybe_stage_entry(
        session,
        event=event,
        market_data=market_data,
        paper_opened=True,
        round_trip_recovery_pct=recovery_pct,
        entry_size_sol=entry_size_sol,
        now=now,
    )
    if not decision.staged:
        return ControlResult(False, f"Manual BUY rejected: {decision.reason}")

    assert decision.position is not None
    assert decision.order is not None
    label = format_token_label(
        token_address,
        symbol=market_data.symbol,
        name=market_data.name,
    )
    return ControlResult(
        True,
        "\n".join(
            [
                "Manual BUY staged",
                f"Position: #{decision.position.id}",
                f"Order: #{decision.order.id}",
                f"Token: {label}",
                f"CA: {short_token_address(token_address)}",
                f"Size: {decision.position.entry_size_sol:g} SOL",
                f"Entry MC: {format_usd(decision.position.entry_market_cap_usd)}",
                (
                    f"TP: +{decision.position.target_profit_pct:g}% -> "
                    f"{format_usd(decision.position.target_market_cap_usd)}"
                ),
                (
                    f"SL: {decision.position.stop_loss_pct:g}% -> "
                    f"{format_usd(decision.position.stop_loss_market_cap_usd)}"
                ),
            ]
        ),
    )


def create_manual_buy_trigger(
    session: Session,
    token_address: str,
    *,
    target_market_cap_usd: float,
    entry_size_sol: float,
) -> ControlResult:
    if entry_size_sol <= 0:
        return ControlResult(False, "Entry size must be positive.")
    trigger = ManualLiveTrigger(
        side="BUY",
        token_address=token_address,
        status="WATCHING",
        target_market_cap_usd=target_market_cap_usd,
        entry_size_sol=entry_size_sol,
        trigger_direction="AT_OR_BELOW",
        created_by="telegram_control_bot",
    )
    session.add(trigger)
    session.flush()
    return ControlResult(
        True,
        "\n".join(
            [
                "Manual BUY trigger created",
                f"Trigger: #{trigger.id}",
                f"CA: {short_token_address(token_address)}",
                f"Target MC: {format_usd(target_market_cap_usd)}",
                f"Size: {entry_size_sol:g} SOL",
            ]
        ),
    )


def create_manual_sell_trigger(
    session: Session,
    token_address: str,
    *,
    target_market_cap_usd: float,
    sell_ratio: float = 100,
) -> ControlResult:
    if sell_ratio != 100:
        return ControlResult(False, "Conditional manual SELL currently supports all/100% only.")
    trigger = ManualLiveTrigger(
        side="SELL",
        token_address=token_address,
        status="WATCHING",
        target_market_cap_usd=target_market_cap_usd,
        sell_ratio=sell_ratio,
        trigger_direction="AT_OR_ABOVE",
        created_by="telegram_control_bot",
    )
    session.add(trigger)
    session.flush()
    return ControlResult(
        True,
        "\n".join(
            [
                "Manual SELL trigger created",
                f"Trigger: #{trigger.id}",
                f"CA: {short_token_address(token_address)}",
                f"Target MC: {format_usd(target_market_cap_usd)}",
                f"Sell: {sell_ratio:g}%",
            ]
        ),
    )


def list_manual_triggers(session: Session) -> str:
    triggers = session.scalars(
        select(ManualLiveTrigger)
        .where(ManualLiveTrigger.status == "WATCHING")
        .order_by(ManualLiveTrigger.created_at.asc(), ManualLiveTrigger.id.asc())
    ).all()
    if not triggers:
        return "Manual triggers: none"
    lines = [f"Manual triggers: {len(triggers)}"]
    for trigger in triggers:
        action = (
            f"BUY {trigger.entry_size_sol:g} SOL"
            if trigger.side == "BUY"
            else f"SELL {trigger.sell_ratio or 100:g}%"
        )
        direction = "<=" if trigger.trigger_direction == "AT_OR_BELOW" else ">="
        lines.extend(
            [
                "",
                f"#{trigger.id} {action}",
                f"CA: {short_token_address(trigger.token_address)}",
                f"MC: {direction} {format_usd(trigger.target_market_cap_usd)}",
            ]
        )
    return "\n".join(lines)


def cancel_manual_trigger(session: Session, trigger_id: int) -> ControlResult:
    trigger = session.get(ManualLiveTrigger, trigger_id)
    if trigger is None:
        return ControlResult(False, f"Trigger #{trigger_id} not found.")
    if trigger.status != "WATCHING":
        return ControlResult(False, f"Trigger #{trigger_id} is {trigger.status}.")
    trigger.status = "CANCELLED"
    trigger.decision_reason = "cancelled_by_telegram_control_bot"
    return ControlResult(True, f"Manual trigger #{trigger_id} cancelled.")


def update_take_profit(session: Session, position_id: int, pct: float) -> ControlResult:
    if pct <= 0:
        return ControlResult(False, "Take-profit percent must be positive.")
    position = session.get(LivePosition, position_id)
    if position is None:
        return ControlResult(False, f"Position #{position_id} not found.")
    if position.status not in {"ENTRY_REQUESTED", "OPEN", "EXIT_REQUESTED"}:
        return ControlResult(False, f"Position #{position_id} is {position.status}.")

    position.target_profit_pct = pct
    position.target_market_cap_usd = position.entry_market_cap_usd * (1 + pct / 100)
    return ControlResult(
        True,
        "\n".join(
            [
                "Live TP updated",
                f"Position: #{position.id}",
                f"Token: {_token_label(session, position.token_address)}",
                f"TP: +{pct:g}% -> {format_usd(position.target_market_cap_usd)}",
            ]
        ),
    )


def update_stop_loss(session: Session, position_id: int, pct: float) -> ControlResult:
    if pct >= 0:
        return ControlResult(False, "Stop-loss percent must be negative, e.g. /sl 5 -70.")
    position = session.get(LivePosition, position_id)
    if position is None:
        return ControlResult(False, f"Position #{position_id} not found.")
    if position.status not in {"ENTRY_REQUESTED", "OPEN", "EXIT_REQUESTED"}:
        return ControlResult(False, f"Position #{position_id} is {position.status}.")

    position.stop_loss_pct = pct
    position.stop_loss_market_cap_usd = position.entry_market_cap_usd * (1 + pct / 100)
    return ControlResult(
        True,
        "\n".join(
            [
                "Live SL updated",
                f"Position: #{position.id}",
                f"Token: {_token_label(session, position.token_address)}",
                f"SL: {pct:g}% -> {format_usd(position.stop_loss_market_cap_usd)}",
            ]
        ),
    )


def format_position_summary(
    session: Session,
    position: LivePosition,
    *,
    include_details: bool = False,
) -> str:
    label = _token_label(session, position.token_address)
    lines = [
        f"#{position.id} {label}",
        f"Status: {position.status}",
        f"CA: {short_token_address(position.token_address)}",
        f"Entry: {position.entry_size_sol:g} SOL @ {format_usd(position.entry_market_cap_usd)}",
        f"TP: +{position.target_profit_pct:g}% -> {format_usd(position.target_market_cap_usd)}",
        f"SL: {position.stop_loss_pct:g}% -> {format_usd(position.stop_loss_market_cap_usd)}",
    ]
    if include_details:
        lines.extend(
            [
                f"Channel: {position.channel_id}",
                f"Highest MC: {format_usd(position.highest_market_cap_usd)}",
                f"Token amount raw: {position.token_amount_raw or 'n/a'}",
                f"Entry time: {position.entry_time}",
            ]
        )
    return "\n".join(lines)


def _has_pending_sell(session: Session, position_id: int) -> bool:
    return bool(
        session.scalar(
            select(LiveOrder.id).where(
                LiveOrder.position_id == position_id,
                LiveOrder.side == "SELL",
                LiveOrder.status.in_(PENDING_ORDER_STATUSES),
            )
        )
    )


def _has_active_token_position(session: Session, token_address: str) -> bool:
    return bool(
        session.scalar(
            select(LivePosition.id).where(
                LivePosition.token_address == token_address,
                LivePosition.status.in_(ACTIVE_POSITION_STATUSES),
            )
        )
    )


def _manual_entry_round_trip_recovery_pct(
    live,
    token_address: str,
    event: TokenCallEvent,
    *,
    entry_size_sol: float | None = None,
) -> float | None:
    if not live.live.get("require_entry_round_trip_quote", False):
        return None
    settings = get_settings()
    if settings.live_execution_adapter != "signer_service":
        return None
    from live.engine import LAMPORTS_PER_SOL
    from live.execution import LiveOrderExecutor

    if entry_size_sol is None:
        entry_size_sol = live._entry_size_sol(event)
    amount = int(entry_size_sol * LAMPORTS_PER_SOL)
    quote = LiveOrderExecutor(settings=settings).signer.quote_buy_round_trip(
        token_address=token_address,
        amount=amount,
    )
    return float(quote["recovery_pct"])


def _token_label(session: Session, token_address: str) -> str:
    snapshot = session.scalars(
        select(TokenMarketSnapshot)
        .where(TokenMarketSnapshot.token_address == token_address)
        .order_by(TokenMarketSnapshot.snapshot_time.desc(), TokenMarketSnapshot.id.desc())
        .limit(1)
    ).first()
    return format_token_label(
        token_address,
        symbol=snapshot.symbol if snapshot else None,
        name=snapshot.name if snapshot else None,
    )
