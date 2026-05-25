from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, MessageContextLink, TelegramMessage
from events.context import MessageContextResolver


TOKEN = "5s7tf6ih2CEZf7ZPNkJAtcknAq9DL5GsWHMMT3Jdpump"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_ca_only_message_links_single_recent_unconsumed_action_context() -> None:
    session = _session()
    now = datetime.utcnow()
    context = TelegramMessage(
        channel_id="marcellcooks",
        message_id="context",
        message_time=now,
        raw_text="entry.\ntek is tekking\nim up to $60 earned in 1 hr",
    )
    ca = TelegramMessage(
        channel_id="marcellcooks",
        message_id="ca",
        message_time=now + timedelta(seconds=23),
        raw_text=TOKEN,
    )
    session.add_all([context, ca])
    session.flush()

    resolution = MessageContextResolver(session, window_seconds=60).resolve(ca, [TOKEN])

    assert resolution.relation == "PRECEDING_ACTION_CONTEXT"
    assert resolution.linked_message is context


def test_ca_with_own_description_does_not_inherit_prior_token_context() -> None:
    session = _session()
    now = datetime.utcnow()
    prior = TelegramMessage(
        channel_id="calls",
        message_id="a",
        message_time=now,
        raw_text="entry on token A",
    )
    self_contained = TelegramMessage(
        channel_id="calls",
        message_id="b",
        message_time=now + timedelta(seconds=30),
        raw_text=f"entry on token B {TOKEN}",
    )
    session.add_all([prior, self_contained])
    session.flush()

    resolution = MessageContextResolver(session).resolve(self_contained, [TOKEN])

    assert resolution.relation is None


def test_five_self_contained_token_posts_30_seconds_apart_never_link_context() -> None:
    session = _session()
    now = datetime.utcnow()
    posts = [
        TelegramMessage(
            channel_id="calls",
            message_id=str(index),
            message_time=now + timedelta(seconds=30 * index),
            raw_text=f"entry token {index} address_{index}",
        )
        for index in range(5)
    ]
    session.add_all(posts)
    session.flush()

    for index, post in enumerate(posts):
        resolution = MessageContextResolver(session).resolve(post, [f"address_{index}"])

        assert resolution.relation is None
        assert resolution.linked_message is None


def test_multiple_unconsumed_action_contexts_are_ambiguous_and_not_linked() -> None:
    session = _session()
    now = datetime.utcnow()
    first = TelegramMessage(channel_id="calls", message_id="1", message_time=now, raw_text="entry token A")
    second = TelegramMessage(
        channel_id="calls",
        message_id="2",
        message_time=now + timedelta(seconds=20),
        raw_text="buying token B",
    )
    ca = TelegramMessage(
        channel_id="calls",
        message_id="3",
        message_time=now + timedelta(seconds=30),
        raw_text=TOKEN,
    )
    session.add_all([first, second, ca])
    session.flush()

    resolution = MessageContextResolver(session).resolve(ca, [TOKEN])

    assert resolution.relation == "AMBIGUOUS_PRECEDING_CONTEXT"
    assert resolution.linked_message is None
    assert {candidate.id for candidate in resolution.candidates} == {first.id, second.id}


def test_already_consumed_context_is_not_attached_to_next_ca() -> None:
    session = _session()
    now = datetime.utcnow()
    context = TelegramMessage(channel_id="calls", message_id="1", message_time=now, raw_text="entry")
    first_ca = TelegramMessage(channel_id="calls", message_id="2", message_time=now + timedelta(seconds=10), raw_text=TOKEN)
    second_ca = TelegramMessage(channel_id="calls", message_id="3", message_time=now + timedelta(seconds=20), raw_text=TOKEN)
    session.add_all([context, first_ca, second_ca])
    session.flush()
    session.add(
        MessageContextLink(
            context_message_db_id=context.id,
            target_message_db_id=first_ca.id,
            token_address=TOKEN,
            context_type="PRECEDING_ACTION_CONTEXT",
        )
    )
    session.flush()

    resolution = MessageContextResolver(session).resolve(second_ca, [TOKEN])

    assert resolution.relation is None
