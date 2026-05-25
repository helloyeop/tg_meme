import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import MessageContextLink, TelegramMessage


ACTION_CONTEXT_PATTERN = re.compile(
    r"\b(entry|entering|buy|buying|bought|ape|aping|aped|aped in|bid|bidding|"
    r"added|adding|loading|loaded|accumulating|in here|back in|round 2)\b",
    re.IGNORECASE,
)


@dataclass
class ContextResolution:
    relation: str | None
    candidates: list[TelegramMessage]

    @property
    def linked_message(self) -> TelegramMessage | None:
        if self.relation == "PRECEDING_ACTION_CONTEXT" and len(self.candidates) == 1:
            return self.candidates[0]
        return None


class MessageContextResolver:
    def __init__(self, session: Session, *, window_seconds: int = 60):
        self.session = session
        self.window_seconds = window_seconds

    def resolve(self, message: TelegramMessage, addresses: list[str]) -> ContextResolution:
        if not _is_ca_only_message(message.raw_text or "", addresses):
            return ContextResolution(None, [])

        lower_bound = message.message_time - timedelta(seconds=self.window_seconds)
        consumed_context = (
            select(MessageContextLink.id)
            .where(MessageContextLink.context_message_db_id == TelegramMessage.id)
            .exists()
        )
        possible_messages = self.session.scalars(
            select(TelegramMessage)
            .where(
                TelegramMessage.channel_id == message.channel_id,
                TelegramMessage.message_time < message.message_time,
                TelegramMessage.message_time >= lower_bound,
                ~TelegramMessage.extracted_addresses.any(),
                ~consumed_context,
            )
            .order_by(TelegramMessage.message_time.desc(), TelegramMessage.id.desc())
        ).all()
        candidates = [
            candidate
            for candidate in possible_messages
            if ACTION_CONTEXT_PATTERN.search(candidate.raw_text or "")
        ]
        if len(candidates) == 1:
            return ContextResolution("PRECEDING_ACTION_CONTEXT", candidates)
        if len(candidates) > 1:
            return ContextResolution("AMBIGUOUS_PRECEDING_CONTEXT", candidates)
        return ContextResolution(None, [])


def _is_ca_only_message(raw_text: str, addresses: list[str]) -> bool:
    if len(addresses) != 1:
        return False
    remainder = raw_text.replace(addresses[0], "")
    remainder = re.sub(r"[\s:;,.|#\-\[\]()]+", "", remainder)
    return remainder.lower() in {"", "ca", "address", "contract"}
