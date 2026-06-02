from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
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
            "take_profit_by_entry_market_cap": {
                "below_500k_pct": 30,
                "from_500k_to_below_1m_pct": 20,
                "at_or_above_1m_pct": 10,
            },
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
    assert decision.position.target_profit_pct == 30
    assert decision.position.target_market_cap_usd == pytest.approx(130000)
    assert session.scalar(select(LiveOrder)).side == "BUY"


@pytest.mark.parametrize(
    ("entry_market_cap_usd", "expected_take_profit_pct"),
    [
        (499_999, 30),
        (500_000, 20),
        (999_999, 20),
        (1_000_000, 10),
    ],
)
def test_live_take_profit_pct_uses_entry_market_cap_tiers(
    entry_market_cap_usd: float,
    expected_take_profit_pct: float,
) -> None:
    live = LiveTradingEngine(live_strategy(), live_settings())

    assert live._take_profit_pct(entry_market_cap_usd) == expected_take_profit_pct


def test_live_recall_entry_uses_reduced_size() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=datetime.utcnow(),
        actionable_signal_count=2,
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
    assert decision.position.entry_size_sol == 0.025
    assert session.scalar(select(LiveOrder)).requested_size_sol == 0.025


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
        quoted_output_lamports=550000000,
    )

    assert decision.staged is True
    assert position.status == "EXIT_REQUESTED"
    orders = session.scalars(select(LiveOrder)).all()
    assert len(orders) == 1
    assert orders[0].side == "SELL"
    assert orders[0].reason == "take_profit_10_pct"


def test_live_take_profit_reason_matches_position_target() -> None:
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
        target_profit_pct=30,
        target_market_cap_usd=130000,
        highest_market_cap_usd=100000,
    )
    session.add(position)
    session.flush()

    decision = LiveTradingEngine(live_strategy(), live_settings()).evaluate_exit(
        session,
        position=position,
        current_market_cap_usd=130000,
        quoted_output_lamports=65000000,
    )

    assert decision.staged is True
    assert session.scalar(select(LiveOrder)).reason == "take_profit_30_pct"


def test_live_entry_refuses_low_round_trip_recovery() -> None:
    strategy = live_strategy()
    strategy["live"]["require_entry_round_trip_quote"] = True
    strategy["live"]["min_entry_round_trip_recovery_pct"] = 90
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

    decision = LiveTradingEngine(strategy, live_settings()).maybe_stage_entry(
        session,
        event=event,
        market_data=TokenMarketData(
            source="test",
            token_address="mint",
            market_cap_usd=100000,
        ),
        paper_opened=True,
        round_trip_recovery_pct=80,
    )

    assert decision.staged is False
    assert decision.reason == "live_entry_round_trip_recovery_too_low"


def test_live_executable_quote_drawdown_does_not_stage_sell_above_emergency_stop() -> None:
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
        entry_size_sol=0.5,
        target_profit_pct=10,
        target_market_cap_usd=110000,
        stop_loss_pct=-70,
        stop_loss_market_cap_usd=30000,
        highest_market_cap_usd=100000,
        entry_input_lamports="500000000",
    )
    session.add(position)
    session.flush()
    decision = LiveTradingEngine(live_strategy(), live_settings()).evaluate_exit(
        session,
        position=position,
        current_market_cap_usd=90000,
        quoted_output_lamports=390000000,
    )

    assert decision.staged is False
    assert decision.reason == "live_exit_threshold_not_reached"
    assert session.scalar(select(LiveOrder)) is None


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


def test_failed_take_profit_sell_reopens_position_for_monitoring() -> None:
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
        status="EXIT_REQUESTED",
        entry_time=datetime.utcnow(),
        entry_market_cap_usd=100000,
        entry_size_sol=0.5,
        target_profit_pct=10,
        target_market_cap_usd=110000,
        highest_market_cap_usd=110000,
        token_amount_raw="123",
        entry_input_lamports="500000000",
        exit_requested_time=datetime.utcnow(),
    )
    session.add(position)
    session.flush()
    order = LiveOrder(
        event_id=event.id,
        position_id=position.id,
        channel_id="channel",
        token_address="mint",
        side="SELL",
        status="STAGED",
        reason="take_profit_10_pct",
        requested_at=datetime.utcnow(),
    )
    session.add(order)
    session.flush()
    signer = SimpleNamespace(
        execute=lambda **_: {
            "status": "FAILED",
            "error": "Jupiter output quote is below the requested minimum.",
        }
    )

    count = LiveOrderExecutor(
        live_settings(live_execution_adapter="signer_service"), signer
    ).execute_staged_orders(session)

    assert count == 0
    assert order.status == "FAILED"
    assert position.status == "OPEN"
    assert position.exit_requested_time is None


def test_recent_failed_take_profit_sell_applies_retry_cooldown() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    now = datetime.utcnow()
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=now,
    )
    session.add(event)
    session.flush()
    position = LivePosition(
        event_id=event.id,
        channel_id="channel",
        token_address="mint",
        status="OPEN",
        entry_time=now,
        entry_market_cap_usd=100000,
        entry_size_sol=0.5,
        target_profit_pct=10,
        target_market_cap_usd=110000,
        highest_market_cap_usd=110000,
    )
    session.add(position)
    session.flush()
    session.add(
        LiveOrder(
            event_id=event.id,
            position_id=position.id,
            channel_id="channel",
            token_address="mint",
            side="SELL",
            status="FAILED",
            reason="take_profit_10_pct",
            requested_at=now - timedelta(seconds=10),
        )
    )
    session.flush()

    decision = LiveTradingEngine(live_strategy(), live_settings()).evaluate_exit(
        session,
        position=position,
        current_market_cap_usd=110000,
        quoted_output_lamports=550000000,
        now=now,
    )

    assert decision.staged is False
    assert decision.reason == "live_take_profit_retry_cooldown"


def test_http_409_take_profit_refusal_reopens_position() -> None:
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
        status="EXIT_REQUESTED",
        entry_time=datetime.utcnow(),
        entry_market_cap_usd=100000,
        entry_size_sol=0.5,
        target_profit_pct=10,
        target_market_cap_usd=110000,
        highest_market_cap_usd=110000,
        token_amount_raw="123",
        entry_input_lamports="500000000",
        exit_requested_time=datetime.utcnow(),
    )
    session.add(position)
    session.flush()
    order = LiveOrder(
        event_id=event.id,
        position_id=position.id,
        channel_id="channel",
        token_address="mint",
        side="SELL",
        status="STAGED",
        reason="take_profit_10_pct",
        requested_at=datetime.utcnow(),
    )
    session.add(order)
    session.flush()
    response = httpx.Response(409, request=httpx.Request("POST", "http://signer/swap"))
    signer = SimpleNamespace(
        execute=lambda **_: (_ for _ in ()).throw(
            httpx.HTTPStatusError("quote refused", request=response.request, response=response)
        )
    )

    count = LiveOrderExecutor(
        live_settings(live_execution_adapter="signer_service"), signer
    ).execute_staged_orders(session)

    assert count == 0
    assert order.status == "FAILED"
    assert position.status == "OPEN"
    assert position.exit_requested_time is None
