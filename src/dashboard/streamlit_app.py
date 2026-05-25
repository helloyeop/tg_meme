from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from sqlalchemy import text

from app.settings import get_settings
from db.session import create_db_engine


st.set_page_config(page_title="Memecoin Telegram Call Bot", layout="wide")
st.title("Memecoin Telegram Call Bot")

settings = get_settings()
engine = create_db_engine(settings.database_url)
strategy = settings.load_strategy_config()
entry_rules = strategy.get("entry", {})
paper_rules = strategy.get("paper", {})
signal_min = entry_rules.get("final_signal_score_min", 55)
risk_min = entry_rules.get("risk_score_min", 65)
liquidity_min = entry_rules.get("min_liquidity_usd", 5000)
sell_slippage_factor = 1 - paper_rules.get("estimated_slippage_pct", 5) / 100

KST_TODAY_START_UTC = "datetime('now', '+9 hours', 'start of day', '-9 hours')"


def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    try:
        return pd.read_sql_query(text(sql), engine, params=params or {})
    except Exception as exc:
        st.warning(f"Query failed: {exc}")
        return pd.DataFrame()


page = st.sidebar.radio(
    "Page",
    [
        "Overview",
        "Live Messages",
        "Context Links",
        "Call Events",
        "Paper Portfolio",
        "Closed Trades",
        "Channel Performance",
        "Token Detail",
        "Settings Preview",
    ],
)

if page == "Overview":
    cols = st.columns(4)
    metrics = {
        "Messages today (KST)": f"select count(*) value from telegram_messages where message_time >= {KST_TODAY_START_UTC}",
        "CAs today (KST)": f"select count(*) value from extracted_addresses where created_at >= {KST_TODAY_START_UTC}",
        "Open events": "select count(*) value from token_call_events where current_status='OPEN'",
        "Open positions": "select count(*) value from paper_positions where status in ('OPEN','PARTIALLY_CLOSED')",
    }
    for col, (label, sql) in zip(cols, metrics.items(), strict=False):
        df = query(sql)
        col.metric(label, int(df.iloc[0]["value"]) if not df.empty else 0)
    cols = st.columns(4)
    llm_metrics = {
        "LLM analyses today": f"""
            select count(*) value from message_analysis
            where created_at >= {KST_TODAY_START_UTC}
        """,
        "Reviewed today": f"""
            select count(*) value from message_analysis
            where was_reviewed=1 and created_at >= {KST_TODAY_START_UTC}
        """,
        "LLM tokens today": f"""
            select coalesce(sum(total_tokens), 0) value from message_analysis
            where created_at >= {KST_TODAY_START_UTC}
        """,
        "Avg latency ms today": f"""
            select coalesce(round(avg(latency_ms)), 0) value from message_analysis
            where created_at >= {KST_TODAY_START_UTC} and latency_ms is not null
        """,
    }
    for col, (label, sql) in zip(cols, llm_metrics.items(), strict=False):
        df = query(sql)
        col.metric(label, int(df.iloc[0]["value"]) if not df.empty else 0)
    cols = st.columns(4)
    operating_metrics = {
        "Awaiting analysis": """
            select (select count(*) from telegram_messages) -
                   (select count(*) from message_analysis) value
        """,
        "GMGN snapshots": "select count(*) value from token_market_snapshots where source='gmgn'",
        "Fast position snapshots": "select count(*) value from token_market_snapshots where source='dexscreener_fast'",
        "App errors": "select count(*) value from app_errors",
    }
    for col, (label, sql) in zip(cols, operating_metrics.items(), strict=False):
        df = query(sql)
        col.metric(label, int(df.iloc[0]["value"]) if not df.empty else 0)
    st.subheader("Recent Errors")
    st.dataframe(
        query(
            """
            select datetime(created_at, '+9 hours') as created_at_kst,
                   component, error_type, error_message
            from app_errors
            order by created_at desc limit 20
            """
        ),
        width="stretch",
    )

elif page == "Live Messages":
    st.dataframe(
        query(
            """
            select datetime(m.message_time, '+9 hours') as message_time_kst,
                   coalesce(ch.title, m.channel_id) as channel_name,
                   m.raw_text, a.token_address, ma.intent,
                   ma.confidence, ma.llm_provider, ma.model_name,
                   ma.initial_model_name, ma.review_model_name, ma.was_reviewed,
                   ma.total_tokens, ma.latency_ms,
                   ma.context_linked, ma.context_relation, ma.context_confidence,
                   case when ma.llm_reason like 'Keyword fallback%' then 1 else 0 end as is_fallback,
                   ma.contains_warning, ma.is_profit_flex, ma.llm_reason
            from telegram_messages m
            left join telegram_channels ch on ch.channel_id=m.channel_id
            left join extracted_addresses a on a.message_db_id=m.id
            left join message_analysis ma on ma.message_db_id=m.id
            order by m.message_time desc
            limit 300
            """
        ),
        width="stretch",
    )

elif page == "Context Links":
    st.dataframe(
        query(
            """
            select datetime(target.message_time, '+9 hours') as target_time_kst,
                   coalesce(ch.title, target.channel_id) as channel_name,
                   links.token_address, links.context_type,
                   round(links.context_delay_seconds, 1) as delay_seconds,
                   links.classification_intent, links.classification_confidence,
                   context.raw_text as preceding_context,
                   target.raw_text as ca_message
            from message_context_links links
            join telegram_messages context on context.id=links.context_message_db_id
            join telegram_messages target on target.id=links.target_message_db_id
            left join telegram_channels ch on ch.channel_id=target.channel_id
            order by links.created_at desc, links.id desc
            limit 300
            """
        ),
        width="stretch",
    )

elif page == "Call Events":
    st.dataframe(
        query(
            """
            with latest_scores as (
              select *, row_number() over (partition by event_id order by score_time desc, id desc) as row_num
              from event_scores
            ),
            latest_market as (
              select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
              from token_market_snapshots
            ),
            open_positions as (
              select event_id, count(*) as open_count
              from paper_positions
              where status in ('OPEN','PARTIALLY_CLOSED')
              group by event_id
            )
            select datetime(e.first_seen_time, '+9 hours') as first_seen_time_kst,
                   datetime(e.first_actionable_call_time, '+9 hours') as first_actionable_call_time_kst,
                   coalesce(ch.title, e.channel_id) as channel_name,
                   e.token_address, e.current_status, e.call_count,
                   e.bullish_update_count, e.warning_count, e.sold_count,
                   e.first_seen_market_cap_usd,
                   e.first_actionable_market_cap_usd, e.actionable_context_type,
                   m.market_cap_usd as current_market_cap_usd,
                   case when e.first_seen_market_cap_usd > 0 and m.market_cap_usd is not null
                        then round(m.market_cap_usd / e.first_seen_market_cap_usd, 2) end as market_cap_multiple,
                   m.liquidity_usd, s.risk_score, round(s.final_signal_score, 2) as final_signal_score,
                   case
                     when coalesce(p.open_count, 0) > 0 then 'paper_open'
                     when s.id is null then 'awaiting_score'
                     when m.market_cap_usd is null then 'missing_market_cap'
                     when s.final_signal_score < :signal_min then 'score_below_threshold'
                     when s.risk_score < :risk_min then 'risk_below_threshold'
                     when coalesce(m.liquidity_usd, 0) < :liquidity_min then 'liquidity_below_threshold'
                     when e.warning_count > 0 then 'event_has_warning'
                     when e.sold_count > 0 then 'event_has_sold'
                     else 'eligible_not_opened'
                   end as paper_entry_status
            from token_call_events e
            left join telegram_channels ch on ch.channel_id=e.channel_id
            left join latest_scores s on s.event_id=e.id and s.row_num=1
            left join latest_market m on m.token_address=e.token_address and m.row_num=1
            left join open_positions p on p.event_id=e.id
            order by e.first_seen_time desc
            limit 300
            """,
            {"signal_min": signal_min, "risk_min": risk_min, "liquidity_min": liquidity_min},
        ),
        width="stretch",
    )

elif page == "Paper Portfolio":
    st.dataframe(
        query(
            """
            with latest_market as (
              select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
              from token_market_snapshots
            )
            select p.id, p.event_id, coalesce(ch.title, p.channel_id) as channel_name,
                   p.token_address, p.status,
                   datetime(entry_time, '+9 hours') as entry_time_kst,
                   entry_market_cap_usd, entry_size_sol, remaining_ratio,
                   m.market_cap_usd as current_market_cap_usd,
                   case when p.entry_market_cap_usd > 0 and m.market_cap_usd is not null
                        then round(m.market_cap_usd / p.entry_market_cap_usd, 2) end as market_cap_multiple,
                   highest_market_cap_usd, stop_loss_market_cap_usd,
                   realized_pnl_sol, unrealized_pnl_sol
            from paper_positions p
            left join telegram_channels ch on ch.channel_id=p.channel_id
            left join latest_market m on m.token_address=p.token_address and m.row_num=1
            where p.status in ('OPEN','PARTIALLY_CLOSED')
            order by p.entry_time desc
            """
        ),
        width="stretch",
    )

elif page == "Closed Trades":
    closed_trades = query(
        """
        select p.id, p.event_id, coalesce(ch.title, p.channel_id) as channel_name,
               p.token_address, p.status,
               datetime(entry_time, '+9 hours') as entry_time_kst,
               datetime(exit_time, '+9 hours') as exit_time_kst,
               entry_market_cap_usd,
               (select f.market_cap_usd from paper_trade_fills f
                where f.position_id=p.id and f.side='SELL'
                order by f.fill_time desc, f.id desc limit 1) as exit_market_cap_usd,
               entry_size_sol, realized_pnl_sol, exit_reason,
               p.post_exit_reference_market_cap_usd,
               p.post_exit_lowest_market_cap_usd,
               datetime(p.post_exit_lowest_time, '+9 hours') as post_exit_lowest_time_kst,
               case when p.post_exit_reference_market_cap_usd > 0
                    then round(100 * ((p.post_exit_lowest_market_cap_usd * :sell_factor)
                         / p.post_exit_reference_market_cap_usd - 1), 2) end
                    as hold_through_worst_return_pct,
               p.post_exit_highest_market_cap_usd,
               datetime(p.post_exit_highest_time, '+9 hours') as post_exit_highest_time_kst,
               case when p.post_exit_reference_market_cap_usd > 0
                    then round(100 * ((p.post_exit_highest_market_cap_usd * :sell_factor)
                         / p.post_exit_reference_market_cap_usd - 1), 2) end
                    as hold_through_peak_return_pct,
               case when p.post_exit_reference_market_cap_usd > 0
                    then round(p.entry_size_sol * ((p.post_exit_highest_market_cap_usd * :sell_factor)
                         / p.post_exit_reference_market_cap_usd - 1), 4) end
                    as hold_through_peak_pnl_sol,
               p.post_exit_latest_market_cap_usd,
               datetime(p.post_exit_latest_snapshot_time, '+9 hours') as post_exit_latest_time_kst,
               p.post_exit_snapshot_count
        from paper_positions p
        left join telegram_channels ch on ch.channel_id=p.channel_id
        where p.status not in ('OPEN','PARTIALLY_CLOSED')
        order by p.exit_time desc
        """,
        {"sell_factor": sell_slippage_factor},
    )
    if not closed_trades.empty:
        selected_trade_id = st.selectbox(
            "Closed trade",
            closed_trades["id"].tolist(),
            format_func=lambda position_id: (
                f"#{position_id} "
                f"{closed_trades.loc[closed_trades['id'] == position_id, 'channel_name'].iloc[0]}"
            ),
        )
        selected_trade = closed_trades.loc[closed_trades["id"] == selected_trade_id].iloc[0]
        cols = st.columns(4)
        cols[0].metric("Realized PnL (SOL)", f"{selected_trade['realized_pnl_sol']:.4f}")
        cols[1].metric(
            "Hold-Through Worst",
            "-" if pd.isna(selected_trade["hold_through_worst_return_pct"]) else f"{selected_trade['hold_through_worst_return_pct']:.2f}%",
        )
        cols[2].metric(
            "Hold-Through Peak",
            "-" if pd.isna(selected_trade["hold_through_peak_return_pct"]) else f"{selected_trade['hold_through_peak_return_pct']:.2f}%",
        )
        cols[3].metric(
            "Peak PnL (SOL)",
            "-" if pd.isna(selected_trade["hold_through_peak_pnl_sol"]) else f"{selected_trade['hold_through_peak_pnl_sol']:.4f}",
        )
    st.dataframe(closed_trades, width="stretch")

elif page == "Channel Performance":
    st.dataframe(query("select * from channel_performance order by overall_score desc"), width="stretch")

elif page == "Token Detail":
    token = st.text_input("Token address")
    if token:
        st.subheader("Events")
        st.dataframe(
            query(
                """
                select e.id, coalesce(ch.title, e.channel_id) as channel_name, e.token_address,
                       datetime(first_seen_time, '+9 hours') as first_seen_time_kst,
                       datetime(first_actionable_call_time, '+9 hours') as first_actionable_call_time_kst,
                       first_seen_market_cap_usd, latest_market_cap_usd,
                       first_actionable_market_cap_usd, actionable_context_type,
                       case when first_seen_market_cap_usd > 0 and latest_market_cap_usd is not null
                            then round(latest_market_cap_usd / first_seen_market_cap_usd, 2) end as market_cap_multiple,
                       current_status, call_count, bullish_update_count, bearish_update_count,
                       warning_count, sold_count
                from token_call_events e
                left join telegram_channels ch on ch.channel_id=e.channel_id
                where e.token_address=:token
                """,
                {"token": token},
            ),
            width="stretch",
        )
        st.subheader("Market Snapshots")
        st.dataframe(
            query(
                """
                select datetime(snapshot_time, '+9 hours') as snapshot_time_kst,
                       source, symbol, name, market_cap_usd, fdv_usd, price_usd,
                       liquidity_usd, volume_5m_usd, price_change_5m_pct, pair_address, dex_name
                from token_market_snapshots where token_address=:token order by snapshot_time desc
                """,
                {"token": token},
            ),
            width="stretch",
        )
        st.subheader("Wallet Activity")
        st.dataframe(
            query(
                """
                select datetime(created_at, '+9 hours') as created_at_kst,
                       source, wallet_address, role, activity_type, amount_token,
                       amount_sol, amount_usd, tx_signature
                from wallet_activity_snapshots where token_address=:token order by created_at desc
                """,
                {"token": token},
            ),
            width="stretch",
        )

elif page == "Settings Preview":
    st.subheader("Runtime")
    st.json(
        {
            "app_env": settings.app_env,
            "dry_run": settings.dry_run,
            "database_url": settings.database_url,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.ollama_model if settings.llm_provider == "ollama" else settings.llm_model,
            "llm_review": {
                "enabled": settings.llm_review_enabled,
                "model": settings.llm_review_model,
                "confidence_below": settings.llm_review_confidence_threshold,
                "intents": sorted(settings.review_intents),
            },
            "llm_fallback_to_ollama": settings.llm_fallback_to_ollama,
            "context_linking": {
                "enabled": settings.context_linking_enabled,
                "window_seconds": settings.context_link_window_seconds,
            },
            "real_trading_enabled": settings.real_trading_enabled,
            "raw_snapshot_storage": {
                "market_json": settings.store_market_snapshot_raw_json,
                "security_json": settings.store_security_snapshot_raw_json,
            },
            "paper_fast_monitor": {
                "enabled": settings.paper_fast_monitor_enabled,
                "seconds": settings.paper_fast_monitor_seconds,
                "max_tokens_per_request": settings.paper_fast_monitor_max_tokens,
                "dexscreener_request_budget_per_minute": settings.dexscreener_request_budget_per_minute,
            },
            "paper_closed_monitor": {
                "enabled": settings.paper_closed_monitor_enabled,
                "seconds": settings.paper_closed_monitor_seconds,
                "max_tokens_per_request": settings.paper_closed_monitor_max_tokens,
                "reuse_recent_market_snapshot": True,
            },
            "entry_thresholds": {
                "final_signal_score_min": signal_min,
                "risk_score_min": risk_min,
                "min_liquidity_usd": liquidity_min,
            },
        }
    )
    for config_path in [Path("config/channels.yaml"), Path("config/channels.example.yaml"), Path("config/strategy.yaml"), Path("config/strategy.example.yaml")]:
        if config_path.exists():
            st.subheader(str(config_path))
            st.code(yaml.safe_dump(yaml.safe_load(config_path.read_text()) or {}, sort_keys=False), language="yaml")
