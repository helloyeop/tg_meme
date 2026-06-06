from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.pipeline import MessagePipeline
from data_sources.types import TokenMarketData, TokenSecurityData, TokenWalletFlowData
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
            "stop_loss_by_entry_market_cap": {
                "below_500k_pct": -35,
                "from_500k_to_below_1m_pct": -30,
                "from_1m_to_below_5m_pct": -25,
                "at_or_above_5m_pct": -20,
            },
            "daily_max_loss_sol": 1,
            "max_open_positions": 3,
            "max_entry_size_sol": 0.5,
            "require_entry_round_trip_quote": False,
            "entry_setup": {
                "enabled": True,
                "observation_seconds": 600,
                "pullback_pct": -20,
                "reclaim_pct": 8,
                "fresh_momentum": {
                    "max_market_cap_usd": 1_000_000,
                    "observation_seconds": 600,
                    "pullback_pct": -20,
                    "reclaim_pct": 8,
                },
                "established_pullback": {
                    "enabled": True,
                    "min_market_cap_usd": 1_000_000,
                    "observation_seconds": 86_400,
                    "pullback_pct": -20,
                    "require_reclaim": False,
                },
                "gmgn_confirmation": {
                    "enabled": True,
                    "allow_missing_activity_data": True,
                    "allow_missing_security_data": True,
                    "require_buy_pressure": False,
                    "min_buy_sell_ratio": 1.1,
                    "min_buys_5m": 1,
                    "min_makers_5m": 5,
                    "max_top10_holder_ratio": 45,
                    "max_dev_wallet_ratio": 5,
                    "block_mint_authority_active": True,
                    "block_freeze_authority_active": True,
                    "block_risk_flags": True,
                },
                "smart_money_confirmation": {
                    "enabled": True,
                    "allow_missing_wallet_flow_data": True,
                    "min_confidence_score": 35,
                    "min_smart_trader_count": 1,
                    "min_smart_net_buy_usd": 0,
                    "min_recent_smart_buy_count": 0,
                    "allow_kol_as_support": True,
                    "min_kol_trader_count": 1,
                    "min_kol_net_buy_usd": 0,
                    "block_on_smart_recent_sell_count": 2,
                    "block_on_negative_smart_net_buy": True,
                },
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
    assert setup.setup_type == "fresh_momentum_reclaim"
    assert setup.trigger_market_cap_usd == 80_000
    assert session.scalar(select(LiveOrder)) is None


def test_live_entry_setup_uses_long_pullback_bid_for_established_tokens(
    monkeypatch,
) -> None:
    _, session = make_session(monkeypatch)
    now = datetime.utcnow()
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=now,
        first_actionable_call_time=now,
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
            market_cap_usd=2_000_000,
        ),
        paper_opened=True,
        decision_reason="opened",
    )

    assert setup is not None
    assert setup.setup_type == "established_pullback_bid"
    assert setup.trigger_market_cap_usd == 1_600_000
    assert (setup.expires_at - now).total_seconds() == pytest.approx(86_400, abs=2)


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


def test_established_pullback_setup_enters_on_pullback_without_reclaim(
    monkeypatch,
) -> None:
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
            setup_type="established_pullback_bid",
            call_time=now,
            call_market_cap_usd=2_000_000,
            trigger_market_cap_usd=1_600_000,
            low_market_cap_usd=1_900_000,
            low_time=now,
            reclaim_market_cap_usd=None,
            expires_at=now + timedelta(hours=24),
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
                    market_cap_usd=1_590_000,
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


def test_live_entry_setup_waits_when_gmgn_buy_pressure_is_weak(monkeypatch) -> None:
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
    pipeline.live.live["entry_setup"]["gmgn_confirmation"]["require_buy_pressure"] = True
    pipeline.live.settings = live_settings()
    pipeline.data_sources = SimpleNamespace(
        dexscreener=SimpleNamespace(
            get_tokens_market_data=lambda _: {
                "mint": TokenMarketData(
                    source="dexscreener_fast",
                    token_address="mint",
                    market_cap_usd=86_000,
                    buys_5m=2,
                    sells_5m=4,
                    makers_5m=9,
                )
            }
        ),
        get_security_data=lambda _: TokenSecurityData(
            source="gmgn",
            token_address="mint",
            top10_holder_ratio=20,
            dev_wallet_ratio=1,
        ),
    )

    count = pipeline.refresh_live_entry_setups(force=True)

    verify_session = session_factory()
    setup = verify_session.scalar(select(LiveEntrySetup))
    order = verify_session.scalar(select(LiveOrder))
    assert count == 1
    assert setup.status == "WATCHING"
    assert setup.decision_reason.startswith("gmgn_buy_sell_ratio_low")
    assert order is None


def test_live_entry_setup_blocks_when_gmgn_security_is_risky(monkeypatch) -> None:
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
                    buys_5m=8,
                    sells_5m=3,
                    makers_5m=9,
                )
            }
        ),
        get_security_data=lambda _: TokenSecurityData(
            source="gmgn",
            token_address="mint",
            top10_holder_ratio=60,
            dev_wallet_ratio=1,
        ),
    )

    count = pipeline.refresh_live_entry_setups(force=True)

    verify_session = session_factory()
    setup = verify_session.scalar(select(LiveEntrySetup))
    order = verify_session.scalar(select(LiveOrder))
    assert count == 1
    assert setup.status == "BLOCKED"
    assert setup.decision_reason.startswith("gmgn_top10_holder_ratio_high")
    assert order is None


def test_live_entry_setup_waits_when_smart_money_is_not_confirmed(monkeypatch) -> None:
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
        ),
        get_wallet_flow_data=lambda _: TokenWalletFlowData(
            source="gmgn",
            token_address="mint",
            confidence_score=0,
        ),
        get_security_data=lambda _: TokenSecurityData(source="gmgn", token_address="mint"),
    )

    count = pipeline.refresh_live_entry_setups(force=True)

    verify_session = session_factory()
    setup = verify_session.scalar(select(LiveEntrySetup))
    order = verify_session.scalar(select(LiveOrder))
    assert count == 1
    assert setup.status == "WATCHING"
    assert setup.decision_reason.startswith("smart_money_not_confirmed")
    assert order is None


def test_live_entry_setup_blocks_when_smart_money_is_selling(monkeypatch) -> None:
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
        ),
        get_wallet_flow_data=lambda _: TokenWalletFlowData(
            source="gmgn",
            token_address="mint",
            smart_net_buy_usd=-5000,
            smart_recent_sell_count=1,
            confidence_score=0,
        ),
        get_security_data=lambda _: TokenSecurityData(source="gmgn", token_address="mint"),
    )

    count = pipeline.refresh_live_entry_setups(force=True)

    verify_session = session_factory()
    setup = verify_session.scalar(select(LiveEntrySetup))
    order = verify_session.scalar(select(LiveOrder))
    assert count == 1
    assert setup.status == "BLOCKED"
    assert setup.decision_reason.startswith("smart_money_net_selling")
    assert order is None
