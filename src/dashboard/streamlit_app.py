from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from sqlalchemy import text

from app.settings import get_settings
from db.session import create_db_engine

st.set_page_config(page_title="Memecoin Telegram Call Bot", layout="wide")

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


PAGE_META = {
    "Overview": (
        "System overview",
        "Collector health, LLM throughput, data freshness, and current paper exposure.",
    ),
    "Live Messages": (
        "Message stream",
        "Recent Telegram messages with extracted CA, LLM interpretation, and context links.",
    ),
    "Context Links": (
        "Context links",
        "CA-only posts matched to nearby explanatory messages in the same channel.",
    ),
    "Call Events": (
        "Call events",
        "Merged channel/token calls with score, market cap movement, and inferred entry status.",
    ),
    "Entry Decisions": (
        "Entry decisions",
        "The exact paper-trading decision stored when a token candidate was evaluated.",
    ),
    "Paper Portfolio": (
        "Paper portfolio",
        "Open and partially closed paper positions monitored against market cap.",
    ),
    "Live Trading": (
        "Live trading",
        "Isolated live ledger with +10% take-profit and -70% emergency stop-loss.",
    ),
    "Closed Trades": (
        "Closed trades",
        "Realized exits compared with post-exit hold-through outcomes.",
    ),
    "Channel Performance": (
        "Channel performance",
        "Channel-level quality scores and realized paper-trading performance.",
    ),
    "Token Detail": (
        "Token detail",
        "Focused event, market, and wallet activity lookup for one Solana CA.",
    ),
    "Settings Preview": (
        "Settings preview",
        "Runtime configuration and local YAML files currently driving the app.",
    ),
}


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #f5f7f4;
            --surface: #ffffff;
            --surface-muted: #eef2ed;
            --ink: #17201b;
            --ink-soft: #425149;
            --muted: #69766f;
            --line: #d9e0da;
            --accent: #20735f;
            --accent-soft: #dceee8;
            --warning: #986b1d;
            --danger: #9a3f3f;
            --success: #20735f;
            --info: #315f86;
            --radius: 8px;
            --shadow-sm: 0 1px 2px rgba(23, 32, 27, 0.06);
        }

        .stApp {
            background: var(--bg);
            color: var(--ink);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
        }

        [data-testid="stSidebar"] {
            background: #101815;
            border-right: 1px solid #22312b;
        }

        [data-testid="stSidebar"] * {
            color: #e6eee9;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: var(--radius);
            margin: 2px 0;
            padding: 2px 6px;
            transition: background 160ms ease-out, color 160ms ease-out;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: #1d2a25;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background: #dceee8;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) * {
            color: #10251f;
            font-weight: 650;
        }

        .block-container {
            max-width: 1560px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }

        .app-header {
            display: flex;
            justify-content: space-between;
            gap: 24px;
            align-items: flex-start;
            padding: 0 0 22px;
            border-bottom: 1px solid var(--line);
            margin-bottom: 20px;
        }

        .app-title {
            margin: 0;
            color: var(--ink);
            font-size: 1.55rem;
            line-height: 1.2;
            font-weight: 760;
            letter-spacing: 0;
        }

        .page-title {
            margin: 4px 0 0;
            color: var(--ink-soft);
            font-size: 1.02rem;
            line-height: 1.4;
            font-weight: 600;
        }

        .page-description {
            max-width: 72ch;
            margin: 8px 0 0;
            color: var(--muted);
            font-size: 0.92rem;
            line-height: 1.55;
        }

        .status-row {
            display: flex;
            flex-wrap: wrap;
            justify-content: flex-end;
            gap: 8px;
            min-width: 280px;
        }

        .status-pill {
            display: inline-flex;
            align-items: center;
            min-height: 28px;
            padding: 4px 9px;
            border: 1px solid var(--line);
            border-radius: 999px;
            background: var(--surface);
            color: var(--ink-soft);
            font-size: 0.78rem;
            font-weight: 650;
            box-shadow: var(--shadow-sm);
            white-space: nowrap;
        }

        .status-pill.good {
            border-color: #bad8ce;
            background: var(--accent-soft);
            color: #164d40;
        }

        .status-pill.warn {
            border-color: #e5d3a8;
            background: #fbf2d8;
            color: #6f4d12;
        }

        div[data-testid="stMetric"] {
            min-height: 104px;
            padding: 15px 16px 13px;
            border: 1px solid var(--line);
            border-radius: var(--radius);
            background: var(--surface);
            box-shadow: var(--shadow-sm);
        }

        div[data-testid="stMetricLabel"] p {
            color: var(--muted);
            font-size: 0.78rem;
            font-weight: 650;
        }

        div[data-testid="stMetricValue"] {
            color: var(--ink);
            font-size: 1.55rem;
            font-weight: 760;
        }

        h2, h3 {
            color: var(--ink);
            letter-spacing: 0;
        }

        h3 {
            margin-top: 1.4rem;
            font-size: 1rem;
            font-weight: 720;
        }

        [data-testid="stDataFrame"] {
            border: 1px solid var(--line);
            border-radius: var(--radius);
            overflow: hidden;
            background: var(--surface);
            box-shadow: var(--shadow-sm);
        }

        [data-testid="stAlert"] {
            border-radius: var(--radius);
        }

        .stTextInput input,
        .stSelectbox [data-baseweb="select"] {
            border-radius: var(--radius);
        }

        div[data-testid="stJson"],
        pre {
            border-radius: var(--radius) !important;
            border: 1px solid var(--line);
        }

        @media (max-width: 800px) {
            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }

            .app-header {
                display: block;
            }

            .status-row {
                justify-content: flex-start;
                margin-top: 14px;
            }
        }

        @media (prefers-reduced-motion: reduce) {
            * {
                transition-duration: 0.01ms !important;
                animation-duration: 0.01ms !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    try:
        return pd.read_sql_query(text(sql), engine, params=params or {})
    except Exception as exc:
        st.warning(f"Query failed: {exc}")
        return pd.DataFrame()


def render_header(current_page: str) -> None:
    page_title, description = PAGE_META[current_page]
    dry_run_class = "good" if settings.dry_run else "warn"
    dry_run_label = "DRY RUN" if settings.dry_run else "LIVE BLOCKED"
    st.markdown(
        f"""
        <div class="app-header">
            <div>
                <h1 class="app-title">Memecoin Telegram Call Bot</h1>
                <div class="page-title">{page_title}</div>
                <p class="page-description">{description}</p>
            </div>
            <div class="status-row" aria-label="Runtime status">
                <span class="status-pill {dry_run_class}">{dry_run_label}</span>
                <span class="status-pill">{settings.llm_provider} / {settings.llm_model}</span>
                <span class="status-pill">{paper_rules.get("entry_size_sol", 0.5)} SOL entries</span>
                <span class="status-pill">{paper_rules.get("daily_max_loss_sol", 0.5)} SOL daily loss</span>
                <span class="status-pill {'warn' if settings.real_trading_enabled else ''}">Live adapter: {settings.live_execution_adapter}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


apply_theme()

page = st.sidebar.radio(
    "Navigation",
    [
        "Overview",
        "Live Messages",
        "Context Links",
        "Call Events",
        "Entry Decisions",
        "Paper Portfolio",
        "Live Trading",
        "Closed Trades",
        "Channel Performance",
        "Token Detail",
        "Settings Preview",
    ],
)
render_header(page)

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
            with latest_market as (
              select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
              from token_market_snapshots
            )
            select datetime(m.message_time, '+9 hours') as message_time_kst,
                   coalesce(ch.title, m.channel_id) as channel_name,
                   m.raw_text, a.token_address, lm.symbol as token_symbol,
                   lm.name as token_name, ma.intent,
                   ma.confidence, ma.llm_provider, ma.model_name,
                   ma.initial_model_name, ma.review_model_name, ma.was_reviewed,
                   ma.total_tokens, ma.latency_ms,
                   ma.context_linked, ma.context_relation, ma.context_confidence,
                   case when ma.llm_reason like 'Keyword fallback%' then 1 else 0 end as is_fallback,
                   ma.contains_warning, ma.is_profit_flex, ma.llm_reason
            from telegram_messages m
            left join telegram_channels ch on ch.channel_id=m.channel_id
            left join extracted_addresses a on a.message_db_id=m.id
            left join latest_market lm on lm.token_address=a.token_address and lm.row_num=1
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
            with latest_market as (
              select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
              from token_market_snapshots
            )
            select datetime(target.message_time, '+9 hours') as target_time_kst,
                   coalesce(ch.title, target.channel_id) as channel_name,
                   links.token_address, lm.symbol as token_symbol,
                   lm.name as token_name, links.context_type,
                   round(links.context_delay_seconds, 1) as delay_seconds,
                   links.classification_intent, links.classification_confidence,
                   context.raw_text as preceding_context,
                   target.raw_text as ca_message
            from message_context_links links
            join telegram_messages context on context.id=links.context_message_db_id
            join telegram_messages target on target.id=links.target_message_db_id
            left join telegram_channels ch on ch.channel_id=target.channel_id
            left join latest_market lm on lm.token_address=links.token_address and lm.row_num=1
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
                   datetime(e.latest_actionable_call_time, '+9 hours') as latest_actionable_call_time_kst,
                   coalesce(ch.title, e.channel_id) as channel_name,
                   e.token_address, m.symbol as token_symbol, m.name as token_name,
                   e.current_status, e.call_count, e.actionable_signal_count,
                   e.bullish_update_count, e.warning_count, e.sold_count,
                   e.first_seen_market_cap_usd,
                   e.first_actionable_market_cap_usd, e.latest_actionable_market_cap_usd,
                   e.latest_actionable_context_type,
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

elif page == "Entry Decisions":
    cols = st.columns(4)
    decision_metrics = {
        "Decisions today (KST)": f"""
            select count(*) value from paper_entry_decisions
            where decision_time >= {KST_TODAY_START_UTC}
        """,
        "Opened today (KST)": f"""
            select count(*) value from paper_entry_decisions
            where opened=1 and decision_time >= {KST_TODAY_START_UTC}
        """,
        "Blocked today (KST)": f"""
            select count(*) value from paper_entry_decisions
            where opened=0 and decision_time >= {KST_TODAY_START_UTC}
        """,
        "Daily loss limit SOL": """
            select coalesce(max(daily_loss_limit_sol), 0) value from paper_entry_decisions
        """,
    }
    for col, (label, sql) in zip(cols, decision_metrics.items(), strict=False):
        df = query(sql)
        value = df.iloc[0]["value"] if not df.empty else 0
        col.metric(label, f"{value:.2f}" if "SOL" in label else int(value or 0))

    st.subheader("Blocked Reason Summary")
    st.dataframe(
        query(
            f"""
            select reason, count(*) as count
            from paper_entry_decisions
            where opened=0 and decision_time >= {KST_TODAY_START_UTC}
            group by reason
            order by count desc
            """
        ),
        width="stretch",
    )
    st.subheader("Recent Entry Decisions")
    st.dataframe(
        query(
            """
            select datetime(d.decision_time, '+9 hours') as decision_time_kst,
                   coalesce(ch.title, d.channel_id) as channel_name,
                   d.token_address, lm.symbol as token_symbol,
                   lm.name as token_name, d.opened, d.reason, d.intent,
                   round(d.final_signal_score, 2) as final_signal_score,
                   round(d.risk_score, 2) as risk_score,
                   d.market_cap_usd, d.liquidity_usd,
                   round(d.daily_loss_sol, 4) as daily_loss_sol,
                   d.daily_loss_limit_sol,
                   p.status as position_status,
                   datetime(m.message_time, '+9 hours') as message_time_kst,
                   m.raw_text
            from paper_entry_decisions d
            left join telegram_channels ch on ch.channel_id=d.channel_id
            left join paper_positions p on p.id=d.position_id
            left join telegram_messages m on m.id=d.message_db_id
            left join (
              select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
              from token_market_snapshots
            ) lm on lm.token_address=d.token_address and lm.row_num=1
            order by d.decision_time desc, d.id desc
            limit 500
            """
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
                   p.token_address, m.symbol as token_symbol, m.name as token_name,
                   p.status,
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

elif page == "Live Trading":
    cols = st.columns(4)
    live_metrics = {
        "Active live positions": """
            select count(*) value from live_positions
            where status in ('ENTRY_REQUESTED','OPEN','EXIT_REQUESTED')
        """,
        "Staged live orders": """
            select count(*) value from live_orders where status='STAGED'
        """,
        "Submitted live orders": """
            select count(*) value from live_orders where status='SUBMITTED'
        """,
        "Confirmed live orders": """
            select count(*) value from live_orders where status='CONFIRMED'
        """,
    }
    for col, (label, sql) in zip(cols, live_metrics.items(), strict=False):
        df = query(sql)
        col.metric(label, int(df.iloc[0]["value"]) if not df.empty else 0)

    if settings.live_execution_adapter == "disabled":
        st.warning(
            "Live transaction submission is disabled. This page shows isolated live order "
            "staging only; no wallet signer or private key is connected."
        )

    st.subheader("Live Positions")
    st.dataframe(
        query(
            """
            with latest_market as (
              select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
              from token_market_snapshots
            )
            select p.id, coalesce(ch.title, p.channel_id) as channel_name,
                   p.token_address, lm.symbol as token_symbol, lm.name as token_name,
                   p.status, datetime(p.entry_time, '+9 hours') as entry_time_kst,
                   p.entry_market_cap_usd, p.entry_size_sol, p.target_profit_pct,
                   p.target_market_cap_usd, p.highest_market_cap_usd,
                   lm.market_cap_usd as current_market_cap_usd,
                   case when p.entry_market_cap_usd > 0 and lm.market_cap_usd is not null
                        then round(100 * (lm.market_cap_usd / p.entry_market_cap_usd - 1), 2)
                   end as current_return_pct,
                   datetime(p.exit_requested_time, '+9 hours') as exit_requested_time_kst,
                   p.realized_pnl_sol
            from live_positions p
            left join telegram_channels ch on ch.channel_id=p.channel_id
            left join latest_market lm on lm.token_address=p.token_address and lm.row_num=1
            order by p.entry_time desc
            """
        ),
        width="stretch",
    )
    st.subheader("Live Orders")
    st.dataframe(
        query(
            """
            with latest_market as (
              select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
              from token_market_snapshots
            )
            select o.id, datetime(o.requested_at, '+9 hours') as requested_at_kst,
                   coalesce(ch.title, o.channel_id) as channel_name,
                   o.token_address, lm.symbol as token_symbol, lm.name as token_name,
                   o.side, o.status, o.reason, o.requested_size_sol,
                   o.reference_market_cap_usd, o.target_market_cap_usd,
                   o.jupiter_request_id, o.transaction_signature
            from live_orders o
            left join telegram_channels ch on ch.channel_id=o.channel_id
            left join latest_market lm on lm.token_address=o.token_address and lm.row_num=1
            order by o.requested_at desc, o.id desc
            limit 500
            """
        ),
        width="stretch",
    )

elif page == "Closed Trades":
    closed_trades = query(
        """
        with latest_market as (
          select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
          from token_market_snapshots
        )
        select p.id, p.event_id, coalesce(ch.title, p.channel_id) as channel_name,
               p.token_address, lm.symbol as token_symbol, lm.name as token_name,
               p.status,
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
        left join latest_market lm on lm.token_address=p.token_address and lm.row_num=1
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
            "-"
            if pd.isna(selected_trade["hold_through_worst_return_pct"])
            else f"{selected_trade['hold_through_worst_return_pct']:.2f}%",
        )
        cols[2].metric(
            "Hold-Through Peak",
            "-"
            if pd.isna(selected_trade["hold_through_peak_return_pct"])
            else f"{selected_trade['hold_through_peak_return_pct']:.2f}%",
        )
        cols[3].metric(
            "Peak PnL (SOL)",
            "-"
            if pd.isna(selected_trade["hold_through_peak_pnl_sol"])
            else f"{selected_trade['hold_through_peak_pnl_sol']:.4f}",
        )
    st.dataframe(closed_trades, width="stretch")

elif page == "Channel Performance":
    st.dataframe(
        query("select * from channel_performance order by overall_score desc"), width="stretch"
    )

elif page == "Token Detail":
    token = st.text_input("Token address")
    if token:
        st.subheader("Events")
        st.dataframe(
            query(
                """
                with latest_market as (
                  select *, row_number() over (partition by token_address order by snapshot_time desc, id desc) as row_num
                  from token_market_snapshots
                )
                select e.id, coalesce(ch.title, e.channel_id) as channel_name,
                       e.token_address, lm.symbol as token_symbol, lm.name as token_name,
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
                left join latest_market lm on lm.token_address=e.token_address and lm.row_num=1
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
            "llm_model": settings.ollama_model
            if settings.llm_provider == "ollama"
            else settings.llm_model,
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
    for config_path in [
        Path("config/channels.yaml"),
        Path("config/channels.example.yaml"),
        Path("config/strategy.yaml"),
        Path("config/strategy.example.yaml"),
    ]:
        if config_path.exists():
            st.subheader(str(config_path))
            st.code(
                yaml.safe_dump(yaml.safe_load(config_path.read_text()) or {}, sort_keys=False),
                language="yaml",
            )
