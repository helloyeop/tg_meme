from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import (
    Base,
    MessageAnalysis,
    TelegramChannel,
    TelegramMessage,
    TokenActionableSignal,
)
from events.manager import CallEventManager


def test_same_channel_same_ca_merges_into_one_event() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    token = "So11111111111111111111111111111111111111112"
    manager = CallEventManager(session)

    first = TelegramMessage(
        channel_id="chan", message_id="1", message_time=datetime.utcnow(), raw_text=token
    )
    second = TelegramMessage(
        channel_id="chan",
        message_id="2",
        message_time=datetime.utcnow(),
        raw_text=f"round 2 {token}",
    )
    analysis = MessageAnalysis(message_db_id=1, intent="BUY_CALL", contains_reentry_phrase=True)
    session.add_all([first, second])
    session.flush()

    event1 = manager.create_or_update_event(
        message=first,
        token_address=token,
        analysis=analysis,
        first_seen_market_cap_usd=125000,
    )
    event2 = manager.create_or_update_event(message=second, token_address=token, analysis=analysis)

    assert event1.id == event2.id
    assert event2.call_count == 2
    assert event2.first_seen_market_cap_usd == 125000


def test_alias_and_name_merge_into_one_event() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    token = "So11111111111111111111111111111111111111112"
    session.add_all(
        [
            TelegramChannel(channel_id="-100100", title="alpha"),
            TelegramChannel(channel_id="alpha", title="alpha"),
        ]
    )
    numeric = TelegramMessage(
        channel_id="-100100", message_id="1", message_time=datetime.utcnow(), raw_text=token
    )
    named = TelegramMessage(
        channel_id="alpha", message_id="2", message_time=datetime.utcnow(), raw_text=token
    )
    session.add_all([numeric, named])
    session.flush()
    manager = CallEventManager(session)

    event1 = manager.create_or_update_event(message=numeric, token_address=token)
    event2 = manager.create_or_update_event(message=named, token_address=token)

    assert event1.id == event2.id


def test_different_channels_keep_separate_first_seen_market_caps() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    token = "So11111111111111111111111111111111111111112"
    first = TelegramMessage(
        channel_id="alpha", message_id="1", message_time=datetime.utcnow(), raw_text=token
    )
    second = TelegramMessage(
        channel_id="beta", message_id="1", message_time=datetime.utcnow(), raw_text=token
    )
    session.add_all([first, second])
    session.flush()
    manager = CallEventManager(session)

    alpha = manager.create_or_update_event(
        message=first,
        token_address=token,
        first_seen_market_cap_usd=100000,
    )
    beta = manager.create_or_update_event(
        message=second,
        token_address=token,
        first_seen_market_cap_usd=160000,
    )

    assert alpha.id != beta.id
    assert alpha.first_seen_market_cap_usd == 100000
    assert beta.first_seen_market_cap_usd == 160000


def test_first_buy_call_sets_actionable_time_on_preexisting_observation_event() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    token = "So11111111111111111111111111111111111111112"
    observed_at = datetime.utcnow() - timedelta(hours=2)
    actionable_at = datetime.utcnow()
    discussion = TelegramMessage(
        channel_id="chan", message_id="1", message_time=observed_at, raw_text=token
    )
    entry = TelegramMessage(
        channel_id="chan", message_id="2", message_time=actionable_at, raw_text=token
    )
    session.add_all([discussion, entry])
    session.flush()
    manager = CallEventManager(session)

    event = manager.create_or_update_event(
        message=discussion,
        token_address=token,
        analysis=MessageAnalysis(message_db_id=discussion.id, intent="DISCUSSION"),
        first_seen_market_cap_usd=100000,
    )
    manager.create_or_update_event(
        message=entry,
        token_address=token,
        analysis=MessageAnalysis(
            message_db_id=entry.id,
            intent="BUY_CALL",
            context_relation="PRECEDING_ACTION_CONTEXT",
        ),
        actionable_market_cap_usd=80000,
    )

    assert event.first_seen_time == observed_at
    assert event.first_actionable_call_time == actionable_at
    assert event.actionable_call_message_db_id == entry.id
    assert event.actionable_context_type == "PRECEDING_ACTION_CONTEXT"
    assert event.first_actionable_market_cap_usd == 80000


def test_explicit_recall_after_cooldown_sets_new_actionable_anchor() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    token = "So11111111111111111111111111111111111111112"
    initial_at = datetime.utcnow() - timedelta(hours=2)
    recall_at = datetime.utcnow()
    initial = TelegramMessage(
        channel_id="chan", message_id="1", message_time=initial_at, raw_text=token
    )
    recall = TelegramMessage(
        channel_id="chan", message_id="2", message_time=recall_at, raw_text=f"round 2 {token}"
    )
    session.add_all([initial, recall])
    session.flush()
    manager = CallEventManager(session)

    event = manager.create_or_update_event(
        message=initial,
        token_address=token,
        analysis=MessageAnalysis(message_db_id=initial.id, intent="BUY_CALL"),
        first_seen_market_cap_usd=100000,
        actionable_market_cap_usd=100000,
    )
    manager.create_or_update_event(
        message=recall,
        token_address=token,
        analysis=MessageAnalysis(
            message_db_id=recall.id,
            intent="BUY_CALL",
            contains_reentry_phrase=True,
        ),
        actionable_market_cap_usd=300000,
    )

    signals = session.query(TokenActionableSignal).order_by(TokenActionableSignal.id).all()
    assert len(signals) == 2
    assert [signal.signal_type for signal in signals] == ["INITIAL", "RECALL"]
    assert event.first_actionable_call_time == initial_at
    assert event.latest_actionable_call_time == recall_at
    assert event.latest_actionable_market_cap_usd == 300000
    assert event.actionable_signal_count == 2
    assert signals[-1].chase_increase_pct == 200


def test_recall_inside_cooldown_does_not_reset_actionable_anchor() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    token = "So11111111111111111111111111111111111111112"
    initial_at = datetime.utcnow()
    initial = TelegramMessage(
        channel_id="chan", message_id="1", message_time=initial_at, raw_text=token
    )
    follow_up = TelegramMessage(
        channel_id="chan",
        message_id="2",
        message_time=initial_at + timedelta(minutes=30),
        raw_text=f"round 2 {token}",
    )
    session.add_all([initial, follow_up])
    session.flush()
    manager = CallEventManager(session)

    event = manager.create_or_update_event(
        message=initial,
        token_address=token,
        analysis=MessageAnalysis(message_db_id=initial.id, intent="BUY_CALL"),
        actionable_market_cap_usd=100000,
    )
    manager.create_or_update_event(
        message=follow_up,
        token_address=token,
        analysis=MessageAnalysis(
            message_db_id=follow_up.id,
            intent="BUY_CALL",
            contains_reentry_phrase=True,
        ),
        actionable_market_cap_usd=150000,
    )

    assert session.query(TokenActionableSignal).count() == 1
    assert event.latest_actionable_call_time == initial_at
    assert event.latest_actionable_market_cap_usd == 100000
