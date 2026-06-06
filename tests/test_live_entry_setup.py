from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.pipeline import MessagePipeline
from data_sources.types import TokenMarketData
from db.models import Base, LiveEntrySetup, LiveOrder, TokenCallEvent


def make_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr("app.pipeline.SessionLocal", session_factory)
    return session_factory, session_factory()


def live_strategy():
    return {
        "live": {
            "entry_size_sol": 0.5,
            "take_profit_pct": 10,
            "take_profit_by_entry_market_cap": {
                "below_500k_pct": 30,
                "from_500k_to_below_1m_pct": 20,
                "at_or_above_1m_pct": 10,
            },
            "stop_loss_pct": -70,
            "daily_max_loss_sol": 1,
            "max_open_positions": 3,
            "max_entry_size_sol": 0.5,
            "require_entry_round_trip_quote": False,
            "entry_setup": {
                "enabled": True,
                "observation_seconds": 600,
                "pullback_pct": -20,
                "reclaim_pct": 8,
            },
        }
    }


def live_settings():
    return SimpleNamespace(
        live_order_staging_enabled=True,
        live_wallet_public_key="wallet",
        live_execution_adapter="disabled",
        paper_fast_monitor_max_tokens=30,
    )


def test_live_entry_setup_replaces_immediate_live_entry(monkeypatch) -> None:
    _, session = make_session(monkeypatch)
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=datetime.utcnow(),
        first_actionable_call_time=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    pipeline = MessagePipeline()
    pipeline.live.strategy = live_strategy()
    pipeline.live.live = live_strategy()["live"]
    monkeypatch.setattr("app.pipeline.get_settings", live_settings)

    setup = pipeline._stage_live_entry_setup(
        session,
        event=event,
        market_data=TokenMarketData(
            source="test",
            token_address="mint",
            market_cap_usd=100_000,
        ),
        paper_opened=True,
        decision_reason="opened",
    )

    assert setup is not None
    assert setup.status == "WATCHING"
    assert setup.trigger_market_cap_usd == 80_000
    assert session.scalar(select(LiveOrder)) is None


def test_live_entry_setup_enters_after_pullback_reclaim(monkeypatch) -> None:
    session_factory, session = make_session(monkeypatch)
    now = datetime.utcnow()
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=now,
        first_actionable_call_time=now,
    )
    session.add(event)
    session.flush()
    session.add(
        LiveEntrySetup(
            event_id=event.id,
            channel_id="channel",
            token_address="mint",
            status="WATCHING",
            setup_type="pullback_reclaim",
            call_time=now,
            call_market_cap_usd=100_000,
            trigger_market_cap_usd=80_000,
            low_market_cap_usd=79_000,
            low_time=now,
            reclaim_market_cap_usd=85_320,
            expires_at=now + timedelta(minutes=10),
        )
    )
    session.commit()

    monkeypatch.setattr("app.pipeline.get_settings", live_settings)
    pipeline = MessagePipeline()
    pipeline.live.strategy = live_strategy()
    pipeline.live.live = live_strategy()["live"]
    pipeline.live.settings = live_settings()
    pipeline.data_sources = SimpleNamespace(
        dexscreener=SimpleNamespace(
            get_tokens_market_data=lambda _: {
                "mint": TokenMarketData(
                    source="dexscreener_fast",
                    token_address="mint",
                    market_cap_usd=86_000,
                )
            }
        )
    )

    count = pipeline.refresh_live_entry_setups(force=True)

    verify_session = session_factory()
    setup = verify_session.scalar(select(LiveEntrySetup))
    order = verify_session.scalar(select(LiveOrder))
    assert count == 1
    assert setup.status == "ENTERED"
    assert order is not None
    assert order.side == "BUY"
