from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from alerts.bot import format_token_label, format_usd, short_token_address
from db.models import LiveControlState, LiveOrder, LivePosition, TokenMarketSnapshot

ENTRY_PAUSED_KEY = "live_entry_paused"
ACTIVE_POSITION_STATUSES = ("ENTRY_REQUESTED", "OPEN", "EXIT_REQUESTED")
PENDING_ORDER_STATUSES = ("STAGED", "SIGNED", "SUBMITTED")


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
