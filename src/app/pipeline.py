import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.settings import get_settings
from data_sources.aggregator import DataSourceAggregator
from db.models import (
    ChannelPerformance,
    LiveEntrySetup,
    LivePosition,
    LiveQuoteAudit,
    PaperPosition,
    TelegramMessage,
    TokenCallEvent,
    TokenMarketSnapshot,
)
from db.repositories import (
    log_app_error,
    store_context_links,
    store_extracted_addresses,
    store_market_snapshot,
    store_message_analysis,
    store_paper_entry_decision,
    store_security_snapshot,
)
from db.session import SessionLocal
from events.context import ContextResolution, MessageContextResolver
from events.manager import CallEventManager
from live.engine import LiveTradingEngine
from live.execution import LAMPORTS_PER_SOL, LiveOrderExecutor
from llm.classifier import LLMClassifier, MessageClassification
from paper.engine import PaperTradingEngine
from scoring.engine import ScoringEngine
from telegram.ca_extractor import extract_solana_addresses

logger = logging.getLogger(__name__)


CA_POST_BLOCKING_INTENTS = {"WARNING", "SOLD", "TAKE_PROFIT", "UPDATE_BEARISH"}


def coerce_ca_post_to_buy_call(
    classification: MessageClassification, addresses: list[str]
) -> MessageClassification:
    """Treat a posted Solana CA as an actionable call unless the message is clearly defensive."""
    if not addresses or classification.intent == "BUY_CALL":
        return classification
    if classification.intent in CA_POST_BLOCKING_INTENTS:
        return classification
    if classification.contains_warning or classification.is_exit_signal:
        return classification

    classification.intent = "BUY_CALL"
    classification.confidence = max(classification.confidence or 0, 0.65)
    classification.sentiment = classification.sentiment or "BULLISH"
    classification.urgency = classification.urgency or "MEDIUM"
    classification.is_new_call = True
    classification.is_follow_up = False
    reason = classification.reason or ""
    suffix = (
        "CA post policy: Solana CA was posted in a monitored call channel, so it "
        "is treated as an actionable BUY_CALL unless explicitly bearish or exit-related."
    )
    classification.reason = f"{reason} {suffix}".strip()
    return classification


class MessagePipeline:
    def __init__(self):
        self.classifier = LLMClassifier()
        self.data_sources = DataSourceAggregator()
        self.scoring = ScoringEngine()
        self.paper = PaperTradingEngine()
        self.live = LiveTradingEngine()
        self.live_executor = LiveOrderExecutor()

    def process_unanalyzed_messages(self, limit: int = 100) -> int:
        processed = 0
        with SessionLocal() as session:
            messages = session.scalars(
                select(TelegramMessage)
                .where(~TelegramMessage.analyses.any())
                .order_by(TelegramMessage.message_time.asc())
                .limit(limit)
            ).all()

            for message in messages:
                message_id = message.id
                try:
                    addresses = extract_solana_addresses(message.raw_text)
                    store_extracted_addresses(
                        session, message_db_id=message.id, addresses=addresses
                    )
                    context = ContextResolution(None, [])
                    settings = get_settings()
                    if addresses and settings.context_linking_enabled:
                        context = MessageContextResolver(
                            session,
                            window_seconds=settings.context_link_window_seconds,
                        ).resolve(message, addresses)
                    linked_message = context.linked_message
                    classification = self.classifier.classify(
                        message.raw_text,
                        addresses,
                        preceding_context=linked_message.raw_text if linked_message else None,
                    )
                    classification = coerce_ca_post_to_buy_call(classification, addresses)
                    if context.relation:
                        classification.context_relation = context.relation
                        classification.context_message_ids = [
                            candidate.id for candidate in context.candidates
                        ]
                    if linked_message:
                        classification.context_linked = True
                        classification.context_confidence = classification.confidence
                    analysis = store_message_analysis(
                        session,
                        message_db_id=message.id,
                        analysis=classification.model_dump(),
                    )
                    if context.relation and addresses:
                        store_context_links(
                            session,
                            target_message_db_id=message.id,
                            token_address=addresses[0],
                            context_type=context.relation,
                            candidates=context.candidates,
                            intent=analysis.intent,
                            confidence=analysis.context_confidence or analysis.confidence,
                            target_time=message.message_time,
                        )
                    manager = CallEventManager(session)
                    for address in addresses:
                        market_data = self.data_sources.get_market_data(address)
                        security_data = self.data_sources.get_security_data(address)
                        if market_data:
                            store_market_snapshot(session, market_data)
                        if security_data:
                            store_security_snapshot(session, security_data)

                        event = manager.create_or_update_event(
                            message=message,
                            token_address=address,
                            analysis=analysis,
                            first_seen_price_usd=market_data.price_usd if market_data else None,
                            first_seen_fdv_usd=market_data.fdv_usd if market_data else None,
                            first_seen_market_cap_usd=market_data.market_cap_usd
                            if market_data
                            else None,
                            first_seen_liquidity_usd=market_data.liquidity_usd
                            if market_data
                            else None,
                            actionable_market_cap_usd=market_data.market_cap_usd
                            if market_data
                            else None,
                        )
                        channel_perf = session.scalar(
                            select(ChannelPerformance).where(
                                ChannelPerformance.channel_id == event.channel_id
                            )
                        )
                        score = self.scoring.score(
                            event=event,
                            analysis=analysis,
                            market_data=market_data,
                            security_data=security_data,
                            channel_performance=channel_perf,
                            ca_count=len(addresses) or 1,
                        )
                        self.scoring.persist(session, event.id, score)
                        decision = self.paper.maybe_open_position(
                            session,
                            event=event,
                            score=score,
                            market_data=market_data,
                        )
                        store_paper_entry_decision(
                            session,
                            event=event,
                            message=message,
                            analysis=analysis,
                            score=score,
                            market_data=market_data,
                            decision=decision,
                        )
                        self._stage_live_entry_setup(
                            session,
                            event=event,
                            market_data=market_data,
                            paper_opened=decision.opened,
                            decision_reason=decision.reason,
                        )
                    session.commit()
                    processed += 1
                except Exception as exc:
                    session.rollback()
                    log_app_error(session, "message_pipeline", exc, {"message_id": message_id})
                    session.commit()
                    logger.exception("Failed to process message %s", message_id)
        return processed

    def refresh_open_events(self, limit: int = 5, force: bool = False) -> int:
        refreshed = 0
        with SessionLocal() as session:
            query = (
                select(TokenCallEvent)
                .where(TokenCallEvent.current_status.in_(["OPEN", "WATCH_RISK"]))
                .order_by(TokenCallEvent.updated_at.asc(), TokenCallEvent.id.asc())
            )
            if not force:
                cutoff = datetime.utcnow() - timedelta(
                    seconds=get_settings().open_event_refresh_seconds
                )
                latest_snapshot_time = (
                    select(func.max(TokenMarketSnapshot.snapshot_time))
                    .where(
                        TokenMarketSnapshot.token_address == TokenCallEvent.token_address,
                        TokenMarketSnapshot.source != "dexscreener_fast",
                    )
                    .correlate(TokenCallEvent)
                    .scalar_subquery()
                )
                query = query.where(
                    (latest_snapshot_time.is_(None)) | (latest_snapshot_time <= cutoff)
                )
            due_events = session.scalars(query.limit(limit)).all()
            token_addresses = list(dict.fromkeys(event.token_address for event in due_events))
            for token_address in token_addresses:
                events = session.scalars(
                    select(TokenCallEvent).where(
                        TokenCallEvent.token_address == token_address,
                        TokenCallEvent.current_status.in_(["OPEN", "WATCH_RISK"]),
                    )
                ).all()
                event_ids = [event.id for event in events]
                try:
                    market_data = self.data_sources.get_market_data(token_address)
                    security_data = self.data_sources.get_security_data(token_address)
                    if market_data:
                        store_market_snapshot(session, market_data)
                        for event in events:
                            event.latest_price_usd = market_data.price_usd
                            event.latest_fdv_usd = market_data.fdv_usd
                            event.latest_market_cap_usd = market_data.market_cap_usd
                            event.latest_liquidity_usd = market_data.liquidity_usd
                    if security_data:
                        store_security_snapshot(session, security_data)
                    if market_data and market_data.market_cap_usd is not None:
                        positions = session.scalars(
                            select(PaperPosition).where(
                                PaperPosition.token_address == token_address,
                                PaperPosition.status.in_(["OPEN", "PARTIALLY_CLOSED"]),
                            )
                        ).all()
                        for position in positions:
                            self.paper.update_position(
                                session,
                                position=position,
                                current_market_cap_usd=market_data.market_cap_usd,
                                current_price_usd=market_data.price_usd,
                            )
                    session.commit()
                    refreshed += len(events)
                except Exception as exc:
                    session.rollback()
                    log_app_error(session, "event_refresh", exc, {"event_ids": event_ids})
                    session.commit()
                    logger.exception("Failed to refresh events %s", event_ids)
        return refreshed

    def refresh_open_positions(self, force: bool = False) -> int:
        settings = get_settings()
        if not settings.paper_fast_monitor_enabled:
            return 0

        refreshed = 0
        with SessionLocal() as session:
            query = (
                select(PaperPosition.token_address)
                .where(PaperPosition.status.in_(["OPEN", "PARTIALLY_CLOSED"]))
                .distinct()
                .order_by(PaperPosition.token_address.asc())
            )
            if not force:
                cutoff = datetime.utcnow() - timedelta(seconds=settings.paper_fast_monitor_seconds)
                latest_snapshot_time = (
                    select(func.max(TokenMarketSnapshot.snapshot_time))
                    .where(
                        TokenMarketSnapshot.token_address == PaperPosition.token_address,
                        TokenMarketSnapshot.source == "dexscreener_fast",
                    )
                    .correlate(PaperPosition)
                    .scalar_subquery()
                )
                query = query.where(
                    (latest_snapshot_time.is_(None)) | (latest_snapshot_time <= cutoff)
                )
            token_addresses = session.scalars(
                query.limit(settings.paper_fast_monitor_max_tokens)
            ).all()
            if not token_addresses:
                return 0

            try:
                market_by_token = self.data_sources.dexscreener.get_tokens_market_data(
                    token_addresses
                )
                for token_address in token_addresses:
                    market_data = market_by_token.get(token_address)
                    if market_data is None:
                        continue
                    market_data.source = "dexscreener_fast"
                    store_market_snapshot(session, market_data)
                    events = session.scalars(
                        select(TokenCallEvent).where(
                            TokenCallEvent.token_address == token_address,
                            TokenCallEvent.current_status.in_(["OPEN", "WATCH_RISK"]),
                        )
                    ).all()
                    for event in events:
                        event.latest_price_usd = market_data.price_usd
                        event.latest_fdv_usd = market_data.fdv_usd
                        event.latest_market_cap_usd = market_data.market_cap_usd
                        event.latest_liquidity_usd = market_data.liquidity_usd
                    if market_data.market_cap_usd is None:
                        continue
                    positions = session.scalars(
                        select(PaperPosition).where(
                            PaperPosition.token_address == token_address,
                            PaperPosition.status.in_(["OPEN", "PARTIALLY_CLOSED"]),
                        )
                    ).all()
                    for position in positions:
                        self.paper.update_position(
                            session,
                            position=position,
                            current_market_cap_usd=market_data.market_cap_usd,
                            current_price_usd=market_data.price_usd,
                        )
                        refreshed += 1
                session.commit()
            except Exception as exc:
                session.rollback()
                log_app_error(
                    session, "position_fast_refresh", exc, {"token_addresses": token_addresses}
                )
                session.commit()
                logger.exception("Failed to fast refresh positions for %s", token_addresses)
        return refreshed

    def refresh_live_positions(self) -> int:
        refreshed = 0
        with SessionLocal() as session:
            positions = session.scalars(
                select(LivePosition).where(LivePosition.status == "OPEN")
            ).all()
            token_addresses = list(dict.fromkeys(position.token_address for position in positions))
            if not token_addresses:
                return 0

            market_by_token = self.data_sources.dexscreener.get_tokens_market_data(
                token_addresses[: get_settings().paper_fast_monitor_max_tokens]
            )
            for position in positions:
                market_data = market_by_token.get(position.token_address)
                if market_data is None or market_data.market_cap_usd is None:
                    continue
                quoted_output_lamports = self._sell_quote_output_lamports(
                    session,
                    position=position,
                )
                self.live.evaluate_exit(
                    session,
                    position=position,
                    current_market_cap_usd=market_data.market_cap_usd,
                    quoted_output_lamports=quoted_output_lamports,
                )
                refreshed += 1
            session.commit()
        return refreshed

    def refresh_live_entry_setups(self, force: bool = False) -> int:
        settings = get_settings()
        setup_config = self.live.live.get("entry_setup", {})
        if not setup_config.get("enabled", False):
            return 0

        refreshed = 0
        now = datetime.utcnow()
        with SessionLocal() as session:
            query = (
                select(LiveEntrySetup)
                .where(LiveEntrySetup.status == "WATCHING")
                .order_by(LiveEntrySetup.created_at.asc(), LiveEntrySetup.id.asc())
            )
            if not force:
                query = query.where(LiveEntrySetup.expires_at >= now)
            setups = session.scalars(
                query.limit(settings.paper_fast_monitor_max_tokens)
            ).all()
            if not setups:
                return 0

            token_addresses = list(dict.fromkeys(setup.token_address for setup in setups))
            market_by_token = self.data_sources.dexscreener.get_tokens_market_data(
                token_addresses
            )
            reclaim_pct = float(setup_config.get("reclaim_pct", 8))
            for setup in setups:
                market_data = market_by_token.get(setup.token_address)
                if setup.expires_at < now:
                    setup.status = "EXPIRED"
                    refreshed += 1
                    continue
                if market_data is None or market_data.market_cap_usd is None:
                    continue

                market_data.source = "dexscreener_fast"
                store_market_snapshot(session, market_data)
                current_market_cap = market_data.market_cap_usd
                if (
                    setup.low_market_cap_usd is None
                    or current_market_cap < setup.low_market_cap_usd
                ):
                    setup.low_market_cap_usd = current_market_cap
                    setup.low_time = now
                    setup.reclaim_market_cap_usd = current_market_cap * (
                        1 + reclaim_pct / 100
                    )

                dip_reached = setup.low_market_cap_usd <= setup.trigger_market_cap_usd
                reclaim_reached = (
                    setup.reclaim_market_cap_usd is not None
                    and current_market_cap >= setup.reclaim_market_cap_usd
                )
                if not (dip_reached and reclaim_reached):
                    refreshed += 1
                    continue

                event = session.get(TokenCallEvent, setup.event_id)
                if event is None:
                    setup.status = "CANCELLED"
                    refreshed += 1
                    continue
                round_trip_recovery_pct = self._entry_round_trip_recovery_pct(
                    session,
                    event=event,
                    paper_opened=True,
                )
                decision = self.live.maybe_stage_entry(
                    session,
                    event=event,
                    market_data=market_data,
                    paper_opened=True,
                    round_trip_recovery_pct=round_trip_recovery_pct,
                )
                if decision.staged:
                    setup.status = "ENTERED"
                    setup.position_id = decision.position.id if decision.position else None
                    setup.order_id = decision.order.id if decision.order else None
                else:
                    setup.decision_reason = decision.reason
                    if decision.reason in {
                        "live_position_already_active",
                        "live_max_open_positions_reached",
                        "live_entry_paused",
                        "live_daily_loss_limit_reached",
                    }:
                        setup.status = "BLOCKED"
                refreshed += 1
            session.commit()
        return refreshed

    def _stage_live_entry_setup(
        self,
        session,
        *,
        event: TokenCallEvent,
        market_data,
        paper_opened: bool,
        decision_reason: str,
    ) -> LiveEntrySetup | None:
        setup_config = self.live.live.get("entry_setup", {})
        settings = get_settings()
        if (
            not setup_config.get("enabled", False)
            or not paper_opened
            or not settings.live_order_staging_enabled
            or market_data is None
            or market_data.market_cap_usd is None
        ):
            return None
        existing = session.scalar(
            select(LiveEntrySetup).where(LiveEntrySetup.event_id == event.id)
        )
        if existing is not None:
            return existing
        now = datetime.utcnow()
        observation_seconds = int(setup_config.get("observation_seconds", 600))
        pullback_pct = float(setup_config.get("pullback_pct", -20))
        trigger_market_cap = market_data.market_cap_usd * (1 + pullback_pct / 100)
        setup = LiveEntrySetup(
            event_id=event.id,
            channel_id=event.channel_id,
            token_address=event.token_address,
            status="WATCHING",
            setup_type="pullback_reclaim",
            call_time=event.latest_actionable_call_time
            or event.first_actionable_call_time
            or event.first_seen_time,
            call_market_cap_usd=market_data.market_cap_usd,
            trigger_market_cap_usd=trigger_market_cap,
            low_market_cap_usd=market_data.market_cap_usd,
            low_time=now,
            expires_at=now + timedelta(seconds=observation_seconds),
            decision_reason=decision_reason,
        )
        session.add(setup)
        session.flush()
        return setup

    def _entry_round_trip_recovery_pct(
        self,
        session,
        *,
        event: TokenCallEvent,
        paper_opened: bool,
    ) -> float | None:
        settings = get_settings()
        if (
            not paper_opened
            or not settings.live_order_staging_enabled
            or settings.live_execution_adapter != "signer_service"
        ):
            return None
        amount = int(self.live._entry_size_sol(event) * LAMPORTS_PER_SOL)
        try:
            quote = self.live_executor.signer.quote_buy_round_trip(
                token_address=event.token_address,
                amount=amount,
            )
        except Exception as exc:
            self._store_live_quote_audit(
                session,
                event_id=event.id,
                token_address=event.token_address,
                quote_type="ENTRY_ROUND_TRIP",
                status="ERROR",
                input_amount=amount,
                reason=str(exc),
            )
            return None
        self._store_live_quote_audit(
            session,
            event_id=event.id,
            token_address=event.token_address,
            quote_type="ENTRY_ROUND_TRIP",
            status="QUOTED",
            input_amount=amount,
            output_amount=int(quote["sell"]["out_amount"]),
            recovery_pct=float(quote["recovery_pct"]),
            quote=quote,
        )
        return float(quote["recovery_pct"])

    def _sell_quote_output_lamports(self, session, *, position: LivePosition) -> int | None:
        if not position.token_amount_raw:
            return None
        try:
            quote = self.live_executor.signer.quote_sell(
                token_address=position.token_address,
                amount=int(position.token_amount_raw),
            )
        except Exception as exc:
            self._store_live_quote_audit(
                session,
                event_id=position.event_id,
                position_id=position.id,
                token_address=position.token_address,
                quote_type="EXIT_SELL",
                status="ERROR",
                input_amount=int(position.token_amount_raw),
                reason=str(exc),
            )
            return None
        output_amount = int(quote["out_amount"])
        entry_input = int(
            position.entry_input_lamports or position.entry_size_sol * LAMPORTS_PER_SOL
        )
        self._store_live_quote_audit(
            session,
            event_id=position.event_id,
            position_id=position.id,
            token_address=position.token_address,
            quote_type="EXIT_SELL",
            status="QUOTED",
            input_amount=int(position.token_amount_raw),
            output_amount=output_amount,
            recovery_pct=100 * output_amount / entry_input,
            quote=quote,
        )
        return output_amount

    @staticmethod
    def _store_live_quote_audit(
        session,
        *,
        event_id: int,
        token_address: str,
        quote_type: str,
        status: str,
        input_amount: int,
        position_id: int | None = None,
        output_amount: int | None = None,
        recovery_pct: float | None = None,
        quote: dict | None = None,
        reason: str | None = None,
    ) -> None:
        detail = quote.get("sell", quote) if quote else {}
        session.add(
            LiveQuoteAudit(
                event_id=event_id,
                position_id=position_id,
                token_address=token_address,
                quote_type=quote_type,
                status=status,
                input_amount_raw=str(input_amount),
                output_amount_raw=str(output_amount) if output_amount is not None else None,
                recovery_pct=recovery_pct,
                price_impact=detail.get("price_impact"),
                slippage_bps=detail.get("slippage_bps"),
                fee_bps=detail.get("fee_bps"),
                reason=reason,
                raw_json=json.dumps(quote) if quote else None,
            )
        )

    def execute_live_orders(self) -> int:
        with SessionLocal() as session:
            executed = self.live_executor.execute_staged_orders(session)
            session.commit()
            return executed

    def refresh_closed_positions(self, force: bool = False) -> int:
        settings = get_settings()
        if not settings.paper_closed_monitor_enabled:
            return 0

        with SessionLocal() as session:
            positions = session.scalars(
                select(PaperPosition)
                .where(
                    PaperPosition.status == "CLOSED",
                    PaperPosition.exit_time.is_not(None),
                )
                .order_by(PaperPosition.exit_time.desc(), PaperPosition.id.desc())
            ).all()
            if not positions:
                return 0

            cutoff = datetime.utcnow() - timedelta(seconds=settings.paper_closed_monitor_seconds)
            token_addresses: list[str] = []
            for position in positions:
                latest_snapshot_time = session.scalar(
                    select(func.max(TokenMarketSnapshot.snapshot_time)).where(
                        TokenMarketSnapshot.token_address == position.token_address,
                        TokenMarketSnapshot.snapshot_time >= position.exit_time,
                    )
                )
                if force or latest_snapshot_time is None or latest_snapshot_time <= cutoff:
                    if position.token_address not in token_addresses:
                        token_addresses.append(position.token_address)

            try:
                if token_addresses:
                    requested_tokens = token_addresses[: settings.paper_closed_monitor_max_tokens]
                    market_by_token = self.data_sources.dexscreener.get_tokens_market_data(
                        requested_tokens
                    )
                    for token_address in requested_tokens:
                        market_data = market_by_token.get(token_address)
                        if market_data is None:
                            continue
                        market_data.source = "dexscreener_post_exit"
                        store_market_snapshot(session, market_data)

                tracked = 0
                for position in positions:
                    latest_snapshot_time = session.scalar(
                        select(func.max(TokenMarketSnapshot.snapshot_time)).where(
                            TokenMarketSnapshot.token_address == position.token_address,
                            TokenMarketSnapshot.snapshot_time >= position.exit_time,
                        )
                    )
                    if (
                        latest_snapshot_time is not None
                        and (
                            position.post_exit_latest_snapshot_time is None
                            or latest_snapshot_time > position.post_exit_latest_snapshot_time
                            or position.post_exit_reference_market_cap_usd is None
                        )
                        and self.paper.sync_post_exit_tracking(session, position)
                    ):
                        tracked += 1
                session.commit()
                return tracked
            except Exception as exc:
                session.rollback()
                log_app_error(
                    session, "closed_position_refresh", exc, {"token_addresses": token_addresses}
                )
                session.commit()
                logger.exception("Failed to refresh closed positions for %s", token_addresses)
                return 0
