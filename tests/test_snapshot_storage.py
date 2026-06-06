from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from data_sources.types import TokenMarketData, TokenSecurityData, TokenWalletFlowData
from db.models import (
    Base,
    TokenMarketSnapshot,
    TokenSecuritySnapshot,
    TokenWalletFlowSnapshot,
)
from db.repositories import (
    store_market_snapshot,
    store_security_snapshot,
    store_wallet_flow_snapshot,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_raw_snapshot_payloads_are_not_stored_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "db.repositories.get_settings",
        lambda: SimpleNamespace(
            store_market_snapshot_raw_json=False,
            store_security_snapshot_raw_json=False,
        ),
    )
    session = _session()

    store_market_snapshot(
        session,
        TokenMarketData(
            source="dexscreener",
            token_address="mint",
            market_cap_usd=100,
            raw={"large": "payload"},
        ),
    )
    store_security_snapshot(
        session,
        TokenSecurityData(
            source="gmgn",
            token_address="mint",
            holder_count=100,
            raw={"large": "payload"},
        ),
    )

    assert session.scalar(select(TokenMarketSnapshot)).raw_json is None
    assert session.scalar(select(TokenSecuritySnapshot)).raw_json is None


def test_raw_snapshot_payloads_can_be_enabled_for_diagnostics(monkeypatch) -> None:
    monkeypatch.setattr(
        "db.repositories.get_settings",
        lambda: SimpleNamespace(
            store_market_snapshot_raw_json=True,
            store_security_snapshot_raw_json=True,
        ),
    )
    session = _session()

    store_market_snapshot(
        session,
        TokenMarketData(source="dexscreener", token_address="mint", raw={"sample": 1}),
    )
    store_security_snapshot(
        session,
        TokenSecurityData(source="gmgn", token_address="mint", raw={"sample": 1}),
    )

    assert session.scalar(select(TokenMarketSnapshot)).raw_json == '{"sample": 1}'
    assert session.scalar(select(TokenSecuritySnapshot)).raw_json == '{"sample": 1}'


def test_wallet_flow_snapshot_is_stored(monkeypatch) -> None:
    monkeypatch.setattr(
        "db.repositories.get_settings",
        lambda: SimpleNamespace(store_security_snapshot_raw_json=False),
    )
    session = _session()

    store_wallet_flow_snapshot(
        session,
        TokenWalletFlowData(
            source="gmgn",
            token_address="mint",
            smart_trader_count=2,
            smart_net_buy_usd=1200,
            kol_trader_count=1,
            confidence_score=42,
        ),
    )

    snapshot = session.scalar(select(TokenWalletFlowSnapshot))
    assert snapshot.smart_trader_count == 2
    assert snapshot.smart_net_buy_usd == 1200
    assert snapshot.kol_trader_count == 1
    assert snapshot.confidence_score == 42
