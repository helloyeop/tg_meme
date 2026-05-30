from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class TelegramChannel(TimestampMixin, Base):
    __tablename__ = "telegram_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    username: Mapped[str | None] = mapped_column(String)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id"),
        Index("idx_messages_channel_time", "channel_id", "message_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    message_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    reply_to_message_id: Mapped[str | None] = mapped_column(String)
    forward_from: Mapped[str | None] = mapped_column(String)
    edit_time: Mapped[datetime | None] = mapped_column(DateTime)
    views: Mapped[int | None] = mapped_column(Integer)
    reactions_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    extracted_addresses: Mapped[list["ExtractedAddress"]] = relationship(back_populates="message")
    analyses: Mapped[list["MessageAnalysis"]] = relationship(back_populates="message")


class ExtractedAddress(Base):
    __tablename__ = "extracted_addresses"
    __table_args__ = (Index("idx_addresses_token", "token_address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_db_id: Mapped[int] = mapped_column(ForeignKey("telegram_messages.id"), nullable=False)
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    chain: Mapped[str] = mapped_column(String, default="solana")
    extraction_method: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    message: Mapped[TelegramMessage] = relationship(back_populates="extracted_addresses")


class MessageAnalysis(Base):
    __tablename__ = "message_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_db_id: Mapped[int] = mapped_column(ForeignKey("telegram_messages.id"), nullable=False)
    token_address: Mapped[str | None] = mapped_column(String)
    intent: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    sentiment: Mapped[str | None] = mapped_column(String)
    urgency: Mapped[str | None] = mapped_column(String)
    is_new_call: Mapped[bool] = mapped_column(Boolean, default=False)
    is_follow_up: Mapped[bool] = mapped_column(Boolean, default=False)
    is_profit_flex: Mapped[bool] = mapped_column(Boolean, default=False)
    is_exit_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    contains_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    contains_reentry_phrase: Mapped[bool] = mapped_column(Boolean, default=False)
    mentioned_symbols_json: Mapped[str | None] = mapped_column(Text)
    llm_reason: Mapped[str | None] = mapped_column(Text)
    raw_llm_json: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String)
    llm_provider: Mapped[str | None] = mapped_column(String)
    initial_model_name: Mapped[str | None] = mapped_column(String)
    review_model_name: Mapped[str | None] = mapped_column(String)
    was_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    review_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    review_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    context_linked: Mapped[bool] = mapped_column(Boolean, default=False)
    context_relation: Mapped[str | None] = mapped_column(String)
    context_confidence: Mapped[float | None] = mapped_column(Float)
    context_message_ids_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    message: Mapped[TelegramMessage] = relationship(back_populates="analyses")


class MessageContextLink(Base):
    __tablename__ = "message_context_links"
    __table_args__ = (
        UniqueConstraint("context_message_db_id"),
        Index("idx_context_target", "target_message_db_id", "token_address"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    context_message_db_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_messages.id"), nullable=False
    )
    target_message_db_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_messages.id"), nullable=False
    )
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    context_type: Mapped[str] = mapped_column(String, nullable=False)
    context_delay_seconds: Mapped[float | None] = mapped_column(Float)
    classification_intent: Mapped[str | None] = mapped_column(String)
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class TokenCallEvent(TimestampMixin, Base):
    __tablename__ = "token_call_events"
    __table_args__ = (
        UniqueConstraint("channel_id", "token_address"),
        Index("idx_events_channel_token", "channel_id", "token_address"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    first_message_db_id: Mapped[int | None] = mapped_column(Integer)
    first_seen_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    first_actionable_call_time: Mapped[datetime | None] = mapped_column(DateTime)
    actionable_call_message_db_id: Mapped[int | None] = mapped_column(Integer)
    actionable_context_type: Mapped[str | None] = mapped_column(String)
    first_seen_price_usd: Mapped[float | None] = mapped_column(Float)
    first_seen_fdv_usd: Mapped[float | None] = mapped_column(Float)
    first_seen_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    first_actionable_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    first_seen_liquidity_usd: Mapped[float | None] = mapped_column(Float)
    latest_price_usd: Mapped[float | None] = mapped_column(Float)
    latest_fdv_usd: Mapped[float | None] = mapped_column(Float)
    latest_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    latest_liquidity_usd: Mapped[float | None] = mapped_column(Float)
    current_status: Mapped[str] = mapped_column(String, default="OPEN")
    last_update_time: Mapped[datetime | None] = mapped_column(DateTime)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    bullish_update_count: Mapped[int] = mapped_column(Integer, default=0)
    bearish_update_count: Mapped[int] = mapped_column(Integer, default=0)
    take_profit_count: Mapped[int] = mapped_column(Integer, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    flex_count: Mapped[int] = mapped_column(Integer, default=0)


class TokenMarketSnapshot(Base):
    __tablename__ = "token_market_snapshots"
    __table_args__ = (Index("idx_market_token_time", "token_address", "snapshot_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    price_usd: Mapped[float | None] = mapped_column(Float)
    fdv_usd: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    volume_5m_usd: Mapped[float | None] = mapped_column(Float)
    volume_1h_usd: Mapped[float | None] = mapped_column(Float)
    volume_6h_usd: Mapped[float | None] = mapped_column(Float)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float)
    price_change_5m_pct: Mapped[float | None] = mapped_column(Float)
    price_change_1h_pct: Mapped[float | None] = mapped_column(Float)
    buys_5m: Mapped[int | None] = mapped_column(Integer)
    sells_5m: Mapped[int | None] = mapped_column(Integer)
    makers_5m: Mapped[int | None] = mapped_column(Integer)
    pair_address: Mapped[str | None] = mapped_column(String)
    dex_name: Mapped[str | None] = mapped_column(String)
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class TokenSecuritySnapshot(Base):
    __tablename__ = "token_security_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    holder_count: Mapped[int | None] = mapped_column(Integer)
    top10_holder_ratio: Mapped[float | None] = mapped_column(Float)
    dev_wallet: Mapped[str | None] = mapped_column(String)
    dev_wallet_ratio: Mapped[float | None] = mapped_column(Float)
    mint_authority_active: Mapped[bool | None] = mapped_column(Boolean)
    freeze_authority_active: Mapped[bool | None] = mapped_column(Boolean)
    liquidity_locked: Mapped[bool | None] = mapped_column(Boolean)
    risk_flags_json: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class WalletActivitySnapshot(Base):
    __tablename__ = "wallet_activity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    wallet_address: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String)
    activity_type: Mapped[str | None] = mapped_column(String)
    amount_token: Mapped[float | None] = mapped_column(Float)
    amount_sol: Mapped[float | None] = mapped_column(Float)
    amount_usd: Mapped[float | None] = mapped_column(Float)
    tx_signature: Mapped[str | None] = mapped_column(String)
    tx_time: Mapped[datetime | None] = mapped_column(DateTime)
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class EventScore(Base):
    __tablename__ = "event_scores"
    __table_args__ = (Index("idx_scores_event_time", "event_id", "score_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("token_call_events.id"), nullable=False)
    score_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    message_score: Mapped[float | None] = mapped_column(Float)
    channel_score: Mapped[float | None] = mapped_column(Float)
    timing_score: Mapped[float | None] = mapped_column(Float)
    price_position_score: Mapped[float | None] = mapped_column(Float)
    market_cap_position_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    final_signal_score: Mapped[float | None] = mapped_column(Float)
    score_breakdown_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class ChannelPerformance(Base):
    __tablename__ = "channel_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    total_events: Mapped[int] = mapped_column(Integer, default=0)
    total_paper_trades: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float | None] = mapped_column(Float)
    avg_max_return_pct: Mapped[float | None] = mapped_column(Float)
    avg_drawdown_pct: Mapped[float | None] = mapped_column(Float)
    avg_entry_delay_seconds: Mapped[float | None] = mapped_column(Float)
    rug_or_warning_rate: Mapped[float | None] = mapped_column(Float)
    exit_signal_accuracy: Mapped[float | None] = mapped_column(Float)
    ca_call_score: Mapped[float] = mapped_column(Float, default=50)
    comment_quality_score: Mapped[float] = mapped_column(Float, default=50)
    timing_score: Mapped[float] = mapped_column(Float, default=50)
    rug_avoidance_score: Mapped[float] = mapped_column(Float, default=50)
    exit_signal_score: Mapped[float] = mapped_column(Float, default=50)
    overall_score: Mapped[float] = mapped_column(Float, default=50)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class PaperPosition(TimestampMixin, Base):
    __tablename__ = "paper_positions"
    __table_args__ = (Index("idx_positions_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("token_call_events.id"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    entry_price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    entry_size_sol: Mapped[float] = mapped_column(Float, nullable=False)
    remaining_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    highest_price_usd: Mapped[float | None] = mapped_column(Float)
    stop_loss_price_usd: Mapped[float | None] = mapped_column(Float)
    highest_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    stop_loss_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    realized_pnl_sol: Mapped[float] = mapped_column(Float, default=0)
    unrealized_pnl_sol: Mapped[float] = mapped_column(Float, default=0)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime)
    exit_reason: Mapped[str | None] = mapped_column(String)
    post_exit_reference_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    post_exit_latest_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    post_exit_highest_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    post_exit_lowest_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    post_exit_latest_snapshot_time: Mapped[datetime | None] = mapped_column(DateTime)
    post_exit_highest_time: Mapped[datetime | None] = mapped_column(DateTime)
    post_exit_lowest_time: Mapped[datetime | None] = mapped_column(DateTime)
    post_exit_snapshot_count: Mapped[int] = mapped_column(Integer, default=0)


class PaperTradeFill(Base):
    __tablename__ = "paper_trade_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("paper_positions.id"), nullable=False)
    fill_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    size_ratio: Mapped[float | None] = mapped_column(Float)
    size_sol: Mapped[float | None] = mapped_column(Float)
    pnl_sol: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class PaperEntryDecision(Base):
    __tablename__ = "paper_entry_decisions"
    __table_args__ = (
        Index("idx_entry_decisions_event_time", "event_id", "decision_time"),
        Index("idx_entry_decisions_reason", "reason"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("token_call_events.id"), nullable=False)
    message_db_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_messages.id"))
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("message_analysis.id"))
    position_id: Mapped[int | None] = mapped_column(ForeignKey("paper_positions.id"))
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    opened: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    intent: Mapped[str | None] = mapped_column(String)
    final_signal_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    daily_loss_sol: Mapped[float | None] = mapped_column(Float)
    daily_loss_limit_sol: Mapped[float | None] = mapped_column(Float)
    score_breakdown_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class LivePosition(TimestampMixin, Base):
    __tablename__ = "live_positions"
    __table_args__ = (Index("idx_live_positions_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("token_call_events.id"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    entry_market_cap_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_size_sol: Mapped[float] = mapped_column(Float, nullable=False)
    target_profit_pct: Mapped[float] = mapped_column(Float, nullable=False)
    target_market_cap_usd: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss_pct: Mapped[float] = mapped_column(Float, nullable=False, default=-70)
    stop_loss_market_cap_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    highest_market_cap_usd: Mapped[float] = mapped_column(Float, nullable=False)
    token_amount_raw: Mapped[str | None] = mapped_column(String)
    entry_input_lamports: Mapped[str | None] = mapped_column(String)
    exit_output_lamports: Mapped[str | None] = mapped_column(String)
    entry_wallet_delta_lamports: Mapped[str | None] = mapped_column(String)
    exit_wallet_delta_lamports: Mapped[str | None] = mapped_column(String)
    exit_requested_time: Mapped[datetime | None] = mapped_column(DateTime)
    exit_confirmed_time: Mapped[datetime | None] = mapped_column(DateTime)
    realized_pnl_sol: Mapped[float] = mapped_column(Float, default=0)


class LiveOrder(Base):
    __tablename__ = "live_orders"
    __table_args__ = (
        Index("idx_live_orders_status", "status"),
        Index("idx_live_orders_event_time", "event_id", "requested_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("token_call_events.id"), nullable=False)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("live_positions.id"))
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    token_address: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    requested_size_sol: Mapped[float | None] = mapped_column(Float)
    reference_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    target_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    jupiter_request_id: Mapped[str | None] = mapped_column(String)
    transaction_signature: Mapped[str | None] = mapped_column(String)
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class AppError(Base):
    __tablename__ = "app_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component: Mapped[str | None] = mapped_column(String)
    error_type: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)
    context_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
