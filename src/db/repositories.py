import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.settings import get_settings
from db.models import (
    AppError,
    ExtractedAddress,
    MessageAnalysis,
    MessageContextLink,
    PaperEntryDecision,
    TelegramChannel,
    TelegramMessage,
    TokenMarketSnapshot,
    TokenSecuritySnapshot,
    TokenWalletFlowSnapshot,
)


def upsert_channel_alias(
    session: Session,
    *,
    channel_id: str,
    name: str,
    username: str | None = None,
    is_private: bool = False,
    enabled: bool = True,
) -> TelegramChannel:
    row = session.scalar(select(TelegramChannel).where(TelegramChannel.channel_id == channel_id))
    if row is None:
        row = TelegramChannel(
            channel_id=channel_id,
            title=name,
            username=username,
            is_private=is_private,
            enabled=enabled,
        )
        session.add(row)
    else:
        row.title = name
        row.username = username or row.username
        row.is_private = is_private
        row.enabled = enabled
    session.flush()
    return row


def upsert_message(
    session: Session,
    *,
    channel_id: str,
    message_id: str,
    message_time: datetime,
    raw_text: str | None,
    normalized_text: str | None = None,
    reply_to_message_id: str | None = None,
    forward_from: str | None = None,
    edit_time: datetime | None = None,
    views: int | None = None,
    reactions_json: str | None = None,
) -> TelegramMessage:
    existing = session.scalar(
        select(TelegramMessage).where(
            TelegramMessage.channel_id == channel_id,
            TelegramMessage.message_id == message_id,
        )
    )
    if existing:
        existing.raw_text = raw_text
        existing.normalized_text = normalized_text
        existing.reply_to_message_id = reply_to_message_id
        existing.forward_from = forward_from
        existing.edit_time = edit_time
        existing.views = views
        existing.reactions_json = reactions_json
        return existing

    message = TelegramMessage(
        channel_id=channel_id,
        message_id=message_id,
        message_time=message_time,
        raw_text=raw_text,
        normalized_text=normalized_text,
        reply_to_message_id=reply_to_message_id,
        forward_from=forward_from,
        edit_time=edit_time,
        views=views,
        reactions_json=reactions_json,
    )
    session.add(message)
    session.flush()
    return message


def store_extracted_addresses(
    session: Session,
    *,
    message_db_id: int,
    addresses: list[str],
    extraction_method: str = "regex_base58_32byte",
) -> list[ExtractedAddress]:
    rows: list[ExtractedAddress] = []
    for address in addresses:
        row = ExtractedAddress(
            message_db_id=message_db_id,
            token_address=address,
            chain="solana",
            extraction_method=extraction_method,
            confidence=1.0,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def store_message_analysis(
    session: Session, *, message_db_id: int, analysis: dict
) -> MessageAnalysis:
    row = MessageAnalysis(
        message_db_id=message_db_id,
        token_address=(analysis.get("mentioned_cas") or [None])[0],
        intent=analysis.get("intent", "UNKNOWN"),
        confidence=analysis.get("confidence"),
        sentiment=analysis.get("sentiment"),
        urgency=analysis.get("urgency"),
        is_new_call=bool(analysis.get("is_new_call", False)),
        is_follow_up=bool(analysis.get("is_follow_up", False)),
        is_profit_flex=bool(analysis.get("is_profit_flex", False)),
        is_exit_signal=bool(analysis.get("is_exit_signal", False)),
        contains_warning=bool(analysis.get("contains_warning", False)),
        contains_reentry_phrase=bool(analysis.get("contains_reentry_phrase", False)),
        mentioned_symbols_json=json.dumps(analysis.get("mentioned_symbols", [])),
        llm_reason=analysis.get("reason"),
        raw_llm_json=json.dumps(analysis, ensure_ascii=False),
        model_name=analysis.get("model_name"),
        llm_provider=analysis.get("llm_provider"),
        initial_model_name=analysis.get("initial_model_name"),
        review_model_name=analysis.get("review_model_name"),
        was_reviewed=bool(analysis.get("was_reviewed", False)),
        prompt_tokens=analysis.get("prompt_tokens"),
        completion_tokens=analysis.get("completion_tokens"),
        total_tokens=analysis.get("total_tokens"),
        review_prompt_tokens=analysis.get("review_prompt_tokens"),
        review_completion_tokens=analysis.get("review_completion_tokens"),
        latency_ms=analysis.get("latency_ms"),
        context_linked=bool(analysis.get("context_linked", False)),
        context_relation=analysis.get("context_relation"),
        context_confidence=analysis.get("context_confidence"),
        context_message_ids_json=json.dumps(analysis.get("context_message_ids", [])),
    )
    session.add(row)
    session.flush()
    return row


def store_context_links(
    session: Session,
    *,
    target_message_db_id: int,
    token_address: str,
    context_type: str,
    candidates,
    intent: str,
    confidence: float | None,
    target_time: datetime,
) -> list[MessageContextLink]:
    rows = []
    for candidate in candidates:
        row = MessageContextLink(
            context_message_db_id=candidate.id,
            target_message_db_id=target_message_db_id,
            token_address=token_address,
            context_type=context_type,
            context_delay_seconds=(target_time - candidate.message_time).total_seconds(),
            classification_intent=intent,
            classification_confidence=confidence,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def store_market_snapshot(session: Session, market_data) -> TokenMarketSnapshot:
    raw_json = (
        json.dumps(market_data.raw, ensure_ascii=False)
        if get_settings().store_market_snapshot_raw_json
        else None
    )
    row = TokenMarketSnapshot(
        token_address=market_data.token_address,
        source=market_data.source,
        snapshot_time=datetime.utcnow(),
        symbol=market_data.symbol,
        name=market_data.name,
        price_usd=market_data.price_usd,
        fdv_usd=market_data.fdv_usd,
        market_cap_usd=market_data.market_cap_usd,
        liquidity_usd=market_data.liquidity_usd,
        volume_5m_usd=market_data.volume_5m_usd,
        volume_1h_usd=market_data.volume_1h_usd,
        volume_6h_usd=market_data.volume_6h_usd,
        volume_24h_usd=market_data.volume_24h_usd,
        price_change_5m_pct=market_data.price_change_5m_pct,
        price_change_1h_pct=market_data.price_change_1h_pct,
        buys_5m=market_data.buys_5m,
        sells_5m=market_data.sells_5m,
        makers_5m=market_data.makers_5m,
        pair_address=market_data.pair_address,
        dex_name=market_data.dex_name,
        raw_json=raw_json,
    )
    session.add(row)
    session.flush()
    return row


def store_security_snapshot(session: Session, security_data) -> TokenSecuritySnapshot:
    raw_json = (
        json.dumps(security_data.raw, ensure_ascii=False)
        if get_settings().store_security_snapshot_raw_json
        else None
    )
    row = TokenSecuritySnapshot(
        token_address=security_data.token_address,
        source=security_data.source,
        snapshot_time=datetime.utcnow(),
        holder_count=security_data.holder_count,
        top10_holder_ratio=security_data.top10_holder_ratio,
        dev_wallet=security_data.dev_wallet,
        dev_wallet_ratio=security_data.dev_wallet_ratio,
        mint_authority_active=security_data.mint_authority_active,
        freeze_authority_active=security_data.freeze_authority_active,
        liquidity_locked=security_data.liquidity_locked,
        risk_flags_json=json.dumps(security_data.risk_flags, ensure_ascii=False),
        raw_json=raw_json,
    )
    session.add(row)
    session.flush()
    return row


def store_wallet_flow_snapshot(session: Session, wallet_flow_data) -> TokenWalletFlowSnapshot:
    raw_json = (
        json.dumps(wallet_flow_data.raw, ensure_ascii=False)
        if get_settings().store_security_snapshot_raw_json
        else None
    )
    row = TokenWalletFlowSnapshot(
        token_address=wallet_flow_data.token_address,
        source=wallet_flow_data.source,
        snapshot_time=datetime.utcnow(),
        smart_trader_count=wallet_flow_data.smart_trader_count,
        smart_net_buy_usd=wallet_flow_data.smart_net_buy_usd,
        smart_buy_volume_usd=wallet_flow_data.smart_buy_volume_usd,
        smart_sell_volume_usd=wallet_flow_data.smart_sell_volume_usd,
        smart_recent_buy_count=wallet_flow_data.smart_recent_buy_count,
        smart_recent_sell_count=wallet_flow_data.smart_recent_sell_count,
        kol_trader_count=wallet_flow_data.kol_trader_count,
        kol_net_buy_usd=wallet_flow_data.kol_net_buy_usd,
        kol_buy_volume_usd=wallet_flow_data.kol_buy_volume_usd,
        kol_sell_volume_usd=wallet_flow_data.kol_sell_volume_usd,
        kol_recent_buy_count=wallet_flow_data.kol_recent_buy_count,
        kol_recent_sell_count=wallet_flow_data.kol_recent_sell_count,
        top_trader_sell_pressure_usd=wallet_flow_data.top_trader_sell_pressure_usd,
        confidence_score=wallet_flow_data.confidence_score,
        raw_json=raw_json,
    )
    session.add(row)
    session.flush()
    return row


def store_paper_entry_decision(
    session: Session,
    *,
    event,
    message: TelegramMessage,
    analysis: MessageAnalysis,
    score,
    market_data,
    decision,
) -> PaperEntryDecision:
    row = PaperEntryDecision(
        event_id=event.id,
        message_db_id=message.id,
        analysis_id=analysis.id,
        position_id=decision.position.id if decision.position else None,
        token_address=event.token_address,
        channel_id=event.channel_id,
        decision_time=datetime.utcnow(),
        opened=decision.opened,
        reason=decision.reason,
        intent=analysis.intent,
        final_signal_score=score.final_signal_score,
        risk_score=score.risk_score,
        market_cap_usd=market_data.market_cap_usd if market_data else None,
        liquidity_usd=market_data.liquidity_usd if market_data else None,
        daily_loss_sol=decision.daily_loss_sol,
        daily_loss_limit_sol=decision.daily_loss_limit_sol,
        score_breakdown_json=json.dumps(score.breakdown, ensure_ascii=False),
    )
    session.add(row)
    session.flush()
    return row


def log_app_error(
    session: Session, component: str, exc: Exception, context: dict | None = None
) -> None:
    session.add(
        AppError(
            component=component,
            error_type=type(exc).__name__,
            error_message=str(exc),
            context_json=json.dumps(context or {}, ensure_ascii=False),
        )
    )
