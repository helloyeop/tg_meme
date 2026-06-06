from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.models import Base, LiveOrder, LivePosition, TokenCallEvent, TokenMarketSnapshot
from live.control import (
    get_position_detail,
    list_live_positions,
    live_entry_paused,
    set_live_entry_paused,
    stage_manual_sell,
    update_stop_loss,
    update_take_profit,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_live_position(session, status: str = "OPEN") -> LivePosition:
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    session.add(
        TokenMarketSnapshot(
            token_address="mint",
            source="test",
            snapshot_time=datetime.utcnow(),
            symbol="BULL",
            name="Bull Token",
            market_cap_usd=120_000,
        )
    )
    position = LivePosition(
        event_id=event.id,
        channel_id="channel",
        token_address="mint",
        status=status,
        entry_time=datetime.utcnow(),
        entry_market_cap_usd=100_000,
        entry_size_sol=0.5,
        target_profit_pct=30,
        target_market_cap_usd=130_000,
        stop_loss_pct=-70,
        stop_loss_market_cap_usd=30_000,
        highest_market_cap_usd=120_000,
        token_amount_raw="123456",
        entry_input_lamports="500000000",
    )
    session.add(position)
    session.flush()
    return position


def test_live_pause_state_round_trips() -> None:
    session = make_session()

    assert live_entry_paused(session) is False

    set_live_entry_paused(session, True, "test")
    session.flush()

    assert live_entry_paused(session) is True
    assert "paused" in list_live_positions(session)


def test_manual_sell_stages_one_pending_sell_order() -> None:
    session = make_session()
    position = add_live_position(session)

    result = stage_manual_sell(session, position.id)

    assert result.ok is True
    assert "Manual SELL staged" in result.message
    assert position.status == "EXIT_REQUESTED"
    order = session.scalar(select(LiveOrder))
    assert order.side == "SELL"
    assert order.status == "STAGED"
    assert order.reason == "manual_sell"

    second = stage_manual_sell(session, position.id)

    assert second.ok is False
    assert "not OPEN" in second.message


def test_update_take_profit_and_stop_loss() -> None:
    session = make_session()
    position = add_live_position(session)

    tp = update_take_profit(session, position.id, 20)
    sl = update_stop_loss(session, position.id, -50)

    assert tp.ok is True
    assert sl.ok is True
    assert position.target_profit_pct == 20
    assert position.target_market_cap_usd == 120_000
    assert position.stop_loss_pct == -50
    assert position.stop_loss_market_cap_usd == 50_000
    detail = get_position_detail(session, position.id)
    assert "Bull Token (BULL)" in detail.message
    assert "TP: +20%" in detail.message
    assert "SL: -50%" in detail.message
