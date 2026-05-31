from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import get_settings
from db.models import Base


def _ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    db_path = Path(database_url.removeprefix(prefix))
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    _ensure_sqlite_parent(url)
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, future=True)
    if url.startswith("sqlite"):
        _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Session:
    return SessionLocal()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_market_cap_columns()


def _migrate_market_cap_columns() -> None:
    if engine.dialect.name != "sqlite":
        return
    required_columns = {
        "message_analysis": {
            "llm_provider": "VARCHAR",
            "initial_model_name": "VARCHAR",
            "review_model_name": "VARCHAR",
            "was_reviewed": "BOOLEAN DEFAULT 0",
            "prompt_tokens": "INTEGER",
            "completion_tokens": "INTEGER",
            "total_tokens": "INTEGER",
            "review_prompt_tokens": "INTEGER",
            "review_completion_tokens": "INTEGER",
            "latency_ms": "FLOAT",
            "context_linked": "BOOLEAN DEFAULT 0",
            "context_relation": "VARCHAR",
            "context_confidence": "FLOAT",
            "context_message_ids_json": "TEXT",
        },
        "token_call_events": {
            "first_seen_market_cap_usd": "FLOAT",
            "latest_market_cap_usd": "FLOAT",
            "first_actionable_call_time": "DATETIME",
            "actionable_call_message_db_id": "INTEGER",
            "actionable_context_type": "VARCHAR",
            "first_actionable_market_cap_usd": "FLOAT",
            "latest_actionable_call_time": "DATETIME",
            "latest_actionable_call_message_db_id": "INTEGER",
            "latest_actionable_context_type": "VARCHAR",
            "latest_actionable_market_cap_usd": "FLOAT",
            "actionable_signal_count": "INTEGER DEFAULT 0",
        },
        "event_scores": {"market_cap_position_score": "FLOAT"},
        "paper_positions": {
            "entry_market_cap_usd": "FLOAT",
            "highest_market_cap_usd": "FLOAT",
            "stop_loss_market_cap_usd": "FLOAT",
            "post_exit_reference_market_cap_usd": "FLOAT",
            "post_exit_latest_market_cap_usd": "FLOAT",
            "post_exit_highest_market_cap_usd": "FLOAT",
            "post_exit_lowest_market_cap_usd": "FLOAT",
            "post_exit_latest_snapshot_time": "DATETIME",
            "post_exit_highest_time": "DATETIME",
            "post_exit_lowest_time": "DATETIME",
            "post_exit_snapshot_count": "INTEGER DEFAULT 0",
        },
        "paper_trade_fills": {"market_cap_usd": "FLOAT"},
        "live_positions": {
            "stop_loss_pct": "FLOAT DEFAULT -70",
            "stop_loss_market_cap_usd": "FLOAT DEFAULT 0",
            "token_amount_raw": "VARCHAR",
            "entry_input_lamports": "VARCHAR",
            "exit_output_lamports": "VARCHAR",
            "entry_wallet_delta_lamports": "VARCHAR",
            "exit_wallet_delta_lamports": "VARCHAR",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in required_columns.items():
            present = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in present:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
        connection.execute(
            text(
                """
                UPDATE token_call_events
                SET latest_market_cap_usd = (
                    SELECT market_cap_usd
                    FROM token_market_snapshots
                    WHERE token_market_snapshots.token_address = token_call_events.token_address
                      AND market_cap_usd IS NOT NULL
                    ORDER BY snapshot_time DESC, id DESC
                    LIMIT 1
                )
                WHERE latest_market_cap_usd IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE token_call_events
                SET latest_actionable_call_time = first_actionable_call_time,
                    latest_actionable_call_message_db_id = actionable_call_message_db_id,
                    latest_actionable_context_type = actionable_context_type,
                    latest_actionable_market_cap_usd = first_actionable_market_cap_usd,
                    actionable_signal_count = 1
                WHERE first_actionable_call_time IS NOT NULL
                  AND latest_actionable_call_time IS NULL
                """
            )
        )
