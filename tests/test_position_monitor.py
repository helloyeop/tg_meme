from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.pipeline import MessagePipeline
from data_sources.types import TokenMarketData
from db.models import Base, PaperPosition, PaperTradeFill, TokenCallEvent, TokenMarketSnapshot


class StubDexScreener:
    def __init__(self, market_cap_usd: float = 70000):
        self.requests: list[list[str]] = []
        self.market_cap_usd = market_cap_usd

    def get_tokens_market_data(self, token_addresses: list[str]) -> dict[str, TokenMarketData]:
        self.requests.append(token_addresses)
        return {
            "mint": TokenMarketData(
                source="dexscreener",
                token_address="mint",
                price_usd=0.01,
                market_cap_usd=self.market_cap_usd,
                liquidity_usd=8000,
            )
        }


def test_fast_monitor_batches_open_positions_and_evaluates_stop_loss(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with test_session() as session:
        event = TokenCallEvent(
            channel_id="channel",
            token_address="mint",
            first_seen_time=datetime.utcnow(),
            current_status="OPEN",
        )
        session.add(event)
        session.flush()
        session.add(
            PaperPosition(
                event_id=event.id,
                channel_id="channel",
                token_address="mint",
                status="OPEN",
                entry_time=datetime.utcnow(),
                entry_price_usd=0.01,
                entry_market_cap_usd=100000,
                entry_size_sol=0.5,
                remaining_ratio=1.0,
                highest_market_cap_usd=100000,
            )
        )
        session.commit()

    settings = SimpleNamespace(
        paper_fast_monitor_enabled=True,
        paper_fast_monitor_seconds=5,
        paper_fast_monitor_max_tokens=30,
    )
    monkeypatch.setattr("app.pipeline.get_settings", lambda: settings)
    monkeypatch.setattr("app.pipeline.SessionLocal", test_session)
    pipeline = MessagePipeline()
    dexscreener = StubDexScreener(market_cap_usd=45000)
    pipeline.data_sources.dexscreener = dexscreener

    refreshed = pipeline.refresh_open_positions(force=True)

    with test_session() as session:
        position = session.scalar(select(PaperPosition))
        snapshot = session.scalar(select(TokenMarketSnapshot))
        sell_fill = session.scalar(select(PaperTradeFill).where(PaperTradeFill.side == "SELL"))
        event = session.scalar(select(TokenCallEvent))
        assert refreshed == 1
        assert dexscreener.requests == [["mint"]]
        assert snapshot is not None and snapshot.source == "dexscreener_fast"
        assert event is not None and event.latest_market_cap_usd == 45000
        assert position is not None and position.status == "CLOSED"
        assert sell_fill is not None and sell_fill.reason == "stop_loss"


def test_closed_monitor_reuses_recent_existing_snapshot_without_new_request(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.utcnow()
    with test_session() as session:
        event = TokenCallEvent(channel_id="channel", token_address="mint", first_seen_time=now)
        session.add(event)
        session.flush()
        position = PaperPosition(
            event_id=event.id,
            channel_id="channel",
            token_address="mint",
            status="CLOSED",
            entry_time=now - timedelta(hours=2),
            exit_time=now - timedelta(hours=1),
            entry_price_usd=0.01,
            entry_market_cap_usd=None,
            entry_size_sol=0.5,
            remaining_ratio=0,
            post_exit_latest_snapshot_time=now - timedelta(minutes=1),
        )
        session.add(position)
        session.add_all(
            [
                TokenMarketSnapshot(
                    token_address="mint",
                    source="dexscreener",
                    snapshot_time=now - timedelta(hours=2, seconds=1),
                    market_cap_usd=100000,
                ),
                TokenMarketSnapshot(
                    token_address="mint",
                    source="dexscreener",
                    snapshot_time=now - timedelta(minutes=50),
                    market_cap_usd=70000,
                ),
                TokenMarketSnapshot(
                    token_address="mint",
                    source="dexscreener_fast",
                    snapshot_time=now - timedelta(minutes=1),
                    market_cap_usd=250000,
                ),
            ]
        )
        session.commit()

    settings = SimpleNamespace(
        paper_closed_monitor_enabled=True,
        paper_closed_monitor_seconds=900,
        paper_closed_monitor_max_tokens=30,
    )
    monkeypatch.setattr("app.pipeline.get_settings", lambda: settings)
    monkeypatch.setattr("app.pipeline.SessionLocal", test_session)
    pipeline = MessagePipeline()
    dexscreener = StubDexScreener()
    pipeline.data_sources.dexscreener = dexscreener

    tracked = pipeline.refresh_closed_positions()

    with test_session() as session:
        position = session.scalar(select(PaperPosition))
        assert tracked == 1
        assert dexscreener.requests == []
        assert position is not None and position.post_exit_lowest_market_cap_usd == 70000
        assert position.post_exit_reference_market_cap_usd == 105000
        assert position.post_exit_highest_market_cap_usd == 250000
        assert position.post_exit_latest_market_cap_usd == 250000
        assert position.post_exit_snapshot_count == 2


def test_closed_monitor_fetches_slow_post_exit_snapshot_when_due(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.utcnow()
    with test_session() as session:
        event = TokenCallEvent(channel_id="channel", token_address="mint", first_seen_time=now)
        session.add(event)
        session.flush()
        session.add(
            PaperPosition(
                event_id=event.id,
                channel_id="channel",
                token_address="mint",
                status="CLOSED",
                entry_time=now - timedelta(hours=2),
                exit_time=now - timedelta(hours=1),
                entry_price_usd=0.01,
                entry_market_cap_usd=100000,
                entry_size_sol=0.5,
                remaining_ratio=0,
            )
        )
        session.commit()

    settings = SimpleNamespace(
        paper_closed_monitor_enabled=True,
        paper_closed_monitor_seconds=900,
        paper_closed_monitor_max_tokens=30,
    )
    monkeypatch.setattr("app.pipeline.get_settings", lambda: settings)
    monkeypatch.setattr("app.pipeline.SessionLocal", test_session)
    pipeline = MessagePipeline()
    dexscreener = StubDexScreener()
    pipeline.data_sources.dexscreener = dexscreener

    tracked = pipeline.refresh_closed_positions(force=True)

    with test_session() as session:
        position = session.scalar(select(PaperPosition))
        snapshot = session.scalar(select(TokenMarketSnapshot))
        assert tracked == 1
        assert dexscreener.requests == [["mint"]]
        assert snapshot is not None and snapshot.source == "dexscreener_post_exit"
        assert position is not None and position.post_exit_latest_market_cap_usd == 70000
