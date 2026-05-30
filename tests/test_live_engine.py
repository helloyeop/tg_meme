from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from data_sources.types import TokenMarketData
from db.models import Base, LiveOrder, LivePosition, TokenCallEvent
from live.engine import LiveTradingEngine
from live.execution import JupiterSwapClient, LiveExecutionDisabled, LiveOrderExecutor


def live_settings(**overrides):
    values = {
        "live_order_staging_enabled": True,
        "live_wallet_public_key": "wallet",
        "live_execution_adapter": "disabled",
        "jupiter_api_key": None,
        "jupiter_swap_base_url": "https://api.jup.ag/swap/v2",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def live_strategy():
    return {
        "live": {
            "entry_size_sol": 0.05,
            "take_profit_pct": 10,
            "stop_loss_pct": -70,
            "daily_max_loss_sol": 1,
            "max_open_positions": 1,
            "max_entry_size_sol": 0.05,
        }
    }


def test_live_entry_staging_is_separate_from_paper_position() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=datetime.utcnow(),
    )
    session.add(event)
    session.flush()

    decision = LiveTradingEngine(live_strategy(), live_settings()).maybe_stage_entry(
        session,
        event=event,
        market_data=TokenMarketData(
            source="test",
            token_address="mint",
            market_cap_usd=100000,
        ),
        paper_opened=True,
    )

    assert decision.staged is True
    assert decision.position is not None
    assert decision.position.status == "ENTRY_REQUESTED"
    assert decision.position.target_market_cap_usd == pytest.approx(110000)
    assert session.scalar(select(LiveOrder)).side == "BUY"


def test_live_take_profit_stages_single_sell_order_at_ten_percent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    position = LivePosition(
        event_id=event.id,
        channel_id="channel",
        token_address="mint",
        status="OPEN",
        entry_time=datetime.utcnow(),
        entry_market_cap_usd=100000,
        entry_size_sol=0.05,
        target_profit_pct=10,
        target_market_cap_usd=110000,
        highest_market_cap_usd=100000,
    )
    session.add(position)
    session.flush()
    live = LiveTradingEngine(live_strategy(), live_settings())

    decision = live.evaluate_exit(
        session,
        position=position,
        current_market_cap_usd=110000,
    )

    assert decision.staged is True
    assert position.status == "EXIT_REQUESTED"
    orders = session.scalars(select(LiveOrder)).all()
    assert len(orders) == 1
    assert orders[0].side == "SELL"
    assert orders[0].reason == "take_profit_10_pct"


def test_live_emergency_stop_loss_stages_sell_order() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    position = LivePosition(
        event_id=event.id,
        channel_id="channel",
        token_address="mint",
        status="OPEN",
        entry_time=datetime.utcnow(),
        entry_market_cap_usd=100000,
        entry_size_sol=0.05,
        target_profit_pct=10,
        target_market_cap_usd=110000,
        stop_loss_pct=-70,
        stop_loss_market_cap_usd=30000,
        highest_market_cap_usd=100000,
    )
    session.add(position)
    session.flush()

    decision = LiveTradingEngine(live_strategy(), live_settings()).evaluate_exit(
        session,
        position=position,
        current_market_cap_usd=30000,
    )

    assert decision.staged is True
    assert session.scalar(select(LiveOrder)).reason == "emergency_stop_loss_70_pct"


def test_jupiter_client_refuses_submission_without_signer_adapter() -> None:
    client = JupiterSwapClient()
    client.settings = live_settings()

    with pytest.raises(LiveExecutionDisabled):
        client.execute_signed_order(signed_transaction="signed", request_id="request")


def test_live_executor_confirms_buy_without_touching_paper_ledger() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    position = LivePosition(
        event_id=event.id,
        channel_id="channel",
        token_address="mint",
        status="ENTRY_REQUESTED",
        entry_time=datetime.utcnow(),
        entry_market_cap_usd=100000,
        entry_size_sol=0.5,
        target_profit_pct=10,
        target_market_cap_usd=110000,
        stop_loss_pct=-70,
        stop_loss_market_cap_usd=30000,
        highest_market_cap_usd=100000,
    )
    session.add(position)
    session.flush()
    session.add(
        LiveOrder(
            event_id=event.id,
            position_id=position.id,
            channel_id="channel",
            token_address="mint",
            side="BUY",
            status="STAGED",
            reason="signal_entry",
            requested_at=datetime.utcnow(),
            requested_size_sol=0.5,
        )
    )
    session.flush()

    signer = SimpleNamespace(
        execute=lambda **_: {
            "status": "Success",
            "signature": "signature",
            "request_id": "request",
            "output_amount": "123",
        }
    )
    count = LiveOrderExecutor(
        live_settings(live_execution_adapter="signer_service"), signer
    ).execute_staged_orders(session)

    assert count == 1
    assert position.status == "OPEN"
    assert position.entry_input_lamports == "500000000"
    assert position.token_amount_raw == "123"


def test_live_take_profit_sell_requires_ten_percent_jupiter_output() -> None:
    position = SimpleNamespace(
        entry_input_lamports="500000000",
        target_profit_pct=10,
    )
    order = SimpleNamespace(side="SELL", reason="take_profit_10_pct")

    minimum = LiveOrderExecutor(live_settings())._min_output_amount(order, position)

    assert minimum == 550000000
