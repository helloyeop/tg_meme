from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data_sources.types import TokenMarketData, TokenSecurityData
from db.models import (
    Base,
    MessageAnalysis,
    PaperEntryDecision,
    PaperTradeFill,
    TelegramMessage,
    TokenCallEvent,
)
from db.repositories import store_paper_entry_decision
from paper.engine import PaperTradingEngine
from scoring.engine import ScoringEngine


def test_scoring_blocks_missing_market_data_with_risk_penalty() -> None:
    event = TokenCallEvent(channel_id="c", token_address="t", first_seen_time=datetime.utcnow())
    analysis = MessageAnalysis(message_db_id=1, intent="BUY_CALL")

    score = ScoringEngine().score(
        event=event, analysis=analysis, market_data=None, security_data=None
    )

    assert score.risk_score < 100
    assert "missing_market_data" in score.breakdown["risk"]["penalties"]


def test_paper_entry_opens_for_valid_score_and_market_data() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    event = TokenCallEvent(
        channel_id="c",
        token_address="t",
        first_seen_time=datetime.utcnow(),
        warning_count=0,
        sold_count=0,
    )
    session.add(event)
    session.flush()
    analysis = MessageAnalysis(message_db_id=1, intent="BUY_CALL")
    market = TokenMarketData(
        source="test",
        token_address="t",
        price_usd=0.01,
        market_cap_usd=100000,
        liquidity_usd=10000,
    )
    security = TokenSecurityData(source="test", token_address="t", holder_count=100)
    score = ScoringEngine().score(
        event=event, analysis=analysis, market_data=market, security_data=security
    )

    decision = PaperTradingEngine().maybe_open_position(
        session, event=event, score=score, market_data=market
    )

    assert decision.opened
    assert decision.position is not None
    assert decision.position.entry_size_sol == 0.5
    assert decision.position.entry_market_cap_usd == 105000


def test_scoring_position_factor_uses_market_cap_not_unit_price() -> None:
    event = TokenCallEvent(
        channel_id="c",
        token_address="t",
        first_seen_time=datetime.utcnow(),
        first_seen_price_usd=1.0,
        first_seen_market_cap_usd=100000,
    )
    analysis = MessageAnalysis(message_db_id=1, intent="BUY_CALL")
    market = TokenMarketData(
        source="test",
        token_address="t",
        price_usd=0.1,
        market_cap_usd=300000,
        liquidity_usd=10000,
    )
    score = ScoringEngine().score(
        event=event,
        analysis=analysis,
        market_data=market,
        security_data=TokenSecurityData(source="test", token_address="t", holder_count=100),
    )

    assert score.market_cap_position_score == 20
    assert score.breakdown["market_cap_position_factor"] == 0.2


def test_scoring_uses_first_actionable_call_time_for_late_buy_signal() -> None:
    now = datetime.utcnow()
    event = TokenCallEvent(
        channel_id="c",
        token_address="t",
        first_seen_time=now - timedelta(hours=4),
        first_actionable_call_time=now,
    )
    analysis = MessageAnalysis(message_db_id=1, intent="BUY_CALL")
    market = TokenMarketData(
        source="test", token_address="t", market_cap_usd=100000, liquidity_usd=10000
    )

    score = ScoringEngine().score(
        event=event,
        analysis=analysis,
        market_data=market,
        security_data=TokenSecurityData(source="test", token_address="t", holder_count=100),
        now=now,
    )

    assert score.timing_score == 100
    assert score.final_signal_score == 70
    assert score.breakdown["timing_basis"] == "first_actionable_call_time"


def test_paper_pnl_uses_market_cap_movement_instead_of_price() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    event = TokenCallEvent(
        channel_id="c",
        token_address="t",
        first_seen_time=datetime.utcnow(),
        warning_count=0,
        sold_count=0,
    )
    session.add(event)
    session.flush()
    analysis = MessageAnalysis(message_db_id=1, intent="BUY_CALL")
    market = TokenMarketData(
        source="test",
        token_address="t",
        price_usd=0.01,
        market_cap_usd=100000,
        liquidity_usd=10000,
    )
    score = ScoringEngine().score(
        event=event,
        analysis=analysis,
        market_data=market,
        security_data=TokenSecurityData(source="test", token_address="t", holder_count=100),
    )
    paper = PaperTradingEngine()
    decision = paper.maybe_open_position(session, event=event, score=score, market_data=market)

    position = decision.position
    assert position is not None
    paper.update_position(
        session,
        position=position,
        current_market_cap_usd=50000,
        current_price_usd=0.01,
    )

    sell_fill = session.query(PaperTradeFill).filter(PaperTradeFill.side == "SELL").one()
    assert position.status == "CLOSED"
    assert sell_fill.market_cap_usd == 47500
    assert round(position.realized_pnl_sol, 6) == -0.27381


def test_paper_daily_loss_uses_kst_calendar_day() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    event = TokenCallEvent(
        channel_id="c",
        token_address="t",
        first_seen_time=datetime(2026, 5, 29, 14, 55),
        warning_count=0,
        sold_count=0,
    )
    session.add(event)
    session.flush()
    session.add(
        PaperTradeFill(
            position_id=1,
            fill_time=datetime(2026, 5, 29, 15, 5),
            side="SELL",
            price_usd=0,
            market_cap_usd=0,
            pnl_sol=-0.4,
            reason="test",
        )
    )
    session.flush()

    paper = PaperTradingEngine()

    assert paper._daily_loss(session, datetime(2026, 5, 29, 16, 0)) == -0.4
    assert paper._daily_loss(session, datetime(2026, 5, 29, 14, 0)) == 0


def test_store_paper_entry_decision_persists_block_reason() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    message = TelegramMessage(
        channel_id="c",
        message_id="1",
        message_time=datetime.utcnow(),
        raw_text="token call",
    )
    event = TokenCallEvent(
        channel_id="c",
        token_address="t",
        first_seen_time=datetime.utcnow(),
        warning_count=0,
        sold_count=0,
    )
    analysis = MessageAnalysis(message_db_id=1, intent="BUY_CALL")
    session.add_all([message, event, analysis])
    session.flush()
    market = TokenMarketData(
        source="test",
        token_address="t",
        price_usd=0.01,
        market_cap_usd=100000,
        liquidity_usd=500,
    )
    score = ScoringEngine().score(
        event=event,
        analysis=analysis,
        market_data=market,
        security_data=TokenSecurityData(source="test", token_address="t", holder_count=100),
    )
    decision = PaperTradingEngine().maybe_open_position(
        session, event=event, score=score, market_data=market
    )

    store_paper_entry_decision(
        session,
        event=event,
        message=message,
        analysis=analysis,
        score=score,
        market_data=market,
        decision=decision,
    )

    row = session.query(PaperEntryDecision).one()
    assert row.opened is False
    assert row.reason == "liquidity_below_threshold"
    assert row.final_signal_score == score.final_signal_score
    assert row.daily_loss_limit_sol == 2
