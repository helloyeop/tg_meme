from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import MessageAnalysis, TelegramChannel, TelegramMessage, TokenCallEvent


INTENT_COUNTERS = {
    "BUY_CALL": "call_count",
    "ADDING": "bullish_update_count",
    "UPDATE_BULLISH": "bullish_update_count",
    "UPDATE_BEARISH": "bearish_update_count",
    "TAKE_PROFIT": "take_profit_count",
    "SOLD": "sold_count",
    "WARNING": "warning_count",
    "FLEX": "flex_count",
}


class CallEventManager:
    def __init__(self, session: Session):
        self.session = session

    def create_or_update_event(
        self,
        *,
        message: TelegramMessage,
        token_address: str,
        analysis: MessageAnalysis | None = None,
        first_seen_price_usd: float | None = None,
        first_seen_fdv_usd: float | None = None,
        first_seen_market_cap_usd: float | None = None,
        first_seen_liquidity_usd: float | None = None,
        actionable_market_cap_usd: float | None = None,
    ) -> TokenCallEvent:
        channel_aliases = self._channel_aliases(message.channel_id)
        event = self.session.scalar(
            select(TokenCallEvent).where(
                TokenCallEvent.channel_id.in_(channel_aliases),
                TokenCallEvent.token_address == token_address,
            )
        )

        if event is None:
            event = TokenCallEvent(
                channel_id=message.channel_id,
                token_address=token_address,
                first_message_db_id=message.id,
                first_seen_time=message.message_time,
                first_seen_price_usd=first_seen_price_usd,
                first_seen_fdv_usd=first_seen_fdv_usd,
                first_seen_market_cap_usd=first_seen_market_cap_usd,
                first_seen_liquidity_usd=first_seen_liquidity_usd,
                last_update_time=message.message_time,
            )
            self.session.add(event)

        event.last_update_time = datetime.utcnow()
        if analysis:
            counter_name = INTENT_COUNTERS.get(analysis.intent)
            if counter_name:
                setattr(event, counter_name, (getattr(event, counter_name) or 0) + 1)
            if analysis.intent in {"SOLD", "WARNING"}:
                event.current_status = "WATCH_RISK"
            if analysis.intent == "BUY_CALL" and event.first_actionable_call_time is None:
                event.first_actionable_call_time = message.message_time
                event.actionable_call_message_db_id = message.id
                event.actionable_context_type = analysis.context_relation or "DIRECT_CA"
                event.first_actionable_market_cap_usd = actionable_market_cap_usd
        else:
            event.call_count = (event.call_count or 0) + 1

        self.session.flush()
        return event

    def _channel_aliases(self, channel_id: str) -> list[str]:
        display_name = self.session.scalar(
            select(TelegramChannel.title).where(TelegramChannel.channel_id == channel_id)
        )
        if not display_name:
            return [channel_id]
        aliases = self.session.scalars(
            select(TelegramChannel.channel_id).where(TelegramChannel.title == display_name)
        ).all()
        return list(dict.fromkeys([channel_id, *aliases]))
