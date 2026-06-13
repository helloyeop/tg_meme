from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from data_sources.types import TokenMarketData
from db.models import (
    Base,
    LiveOrder,
    LivePosition,
    ManualLiveTrigger,
    TokenCallEvent,
    TokenMarketSnapshot,
)
from live.control import (
    cancel_manual_trigger,
    create_manual_buy_trigger,
    create_manual_sell_trigger,
    get_position_detail,
    list_live_positions,
    live_entry_paused,
    set_live_entry_paused,
    stage_manual_buy,
    stage_manual_sell,
    update_stop_loss,
    update_take_profit,
)
from live.control_bot import (
    LiveControlBot,
    _format_live_balance,
    _looks_like_sol_arg,
    _parse_market_cap_arg,
    _parse_sol_arg,
    _parse_token_address_arg,
    get_live_balance,
)
from live.engine import LiveTradingEngine

TOKEN = "5s7tf6ih2CEZf7ZPNkJAtcknAq9DL5GsWHMMT3Jdpump"


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class LiveSettings:
    telegram_alert_bot_token = "token"
    telegram_alert_chat_id = "chat"
    live_order_staging_enabled = True
    live_wallet_public_key = "wallet"
    live_execution_adapter = "disabled"
    live_signer_base_url = "http://signer:8787"
    live_fee_reserve_sol = 0.05
    jupiter_api_key = None
    jupiter_swap_base_url = "https://api.jup.ag/swap/v2"

    @staticmethod
    def load_strategy_config():
        return live_strategy()


def live_strategy():
    return {
        "live": {
            "entry_size_sol": 0.05,
            "take_profit_pct": 10,
            "take_profit_by_entry_market_cap": {
                "below_500k_pct": 30,
                "from_500k_to_below_1m_pct": 20,
                "at_or_above_1m_pct": 10,
            },
            "stop_loss_pct": -70,
            "stop_loss_by_entry_market_cap": {
                "below_500k_pct": -35,
                "from_500k_to_below_1m_pct": -30,
                "from_1m_to_below_5m_pct": -25,
                "at_or_above_5m_pct": -20,
            },
            "daily_max_loss_sol": 1,
            "max_open_positions": 1,
            "max_entry_size_sol": 0.05,
        }
    }


def add_live_position(session, status: str = "OPEN") -> LivePosition:
    event = TokenCallEvent(
        channel_id="channel",
        token_address="mint",
        first_seen_time=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    session.add(
        TokenMarketSnapshot(
            token_address="mint",
            source="test",
            snapshot_time=datetime.utcnow(),
            symbol="BULL",
            name="Bull Token",
            market_cap_usd=120_000,
        )
    )
    position = LivePosition(
        event_id=event.id,
        channel_id="channel",
        token_address="mint",
        status=status,
        entry_time=datetime.utcnow(),
        entry_market_cap_usd=100_000,
        entry_size_sol=0.5,
        target_profit_pct=30,
        target_market_cap_usd=130_000,
        stop_loss_pct=-70,
        stop_loss_market_cap_usd=30_000,
        highest_market_cap_usd=120_000,
        token_amount_raw="123456",
        entry_input_lamports="500000000",
    )
    session.add(position)
    session.flush()
    return position


def test_live_pause_state_round_trips() -> None:
    session = make_session()

    assert live_entry_paused(session) is False

    set_live_entry_paused(session, True, "test")
    session.flush()

    assert live_entry_paused(session) is True
    assert "paused" in list_live_positions(session)


def test_manual_sell_stages_one_pending_sell_order() -> None:
    session = make_session()
    position = add_live_position(session)

    result = stage_manual_sell(session, position.id)

    assert result.ok is True
    assert "Manual SELL staged" in result.message
    assert position.status == "EXIT_REQUESTED"
    order = session.scalar(select(LiveOrder))
    assert order.side == "SELL"
    assert order.status == "STAGED"
    assert order.reason == "manual_sell"

    second = stage_manual_sell(session, position.id)

    assert second.ok is False
    assert "not OPEN" in second.message


def test_update_take_profit_and_stop_loss() -> None:
    session = make_session()
    position = add_live_position(session)

    tp = update_take_profit(session, position.id, 20)
    sl = update_stop_loss(session, position.id, -50)

    assert tp.ok is True
    assert sl.ok is True
    assert position.target_profit_pct == 20
    assert position.target_market_cap_usd == 120_000
    assert position.stop_loss_pct == -50
    assert position.stop_loss_market_cap_usd == 50_000
    detail = get_position_detail(session, position.id)
    assert "Bull Token (BULL)" in detail.message
    assert "TP: +20%" in detail.message
    assert "SL: -50%" in detail.message


class FakeDataSources:
    def __init__(self, market_cap_usd: float = 600_000):
        self.market_cap_usd = market_cap_usd

    def get_market_data(self, token_address: str) -> TokenMarketData:
        return TokenMarketData(
            source="test",
            token_address=token_address,
            symbol="MAN",
            name="Manual Token",
            market_cap_usd=self.market_cap_usd,
        )


def test_manual_buy_stages_live_buy_with_existing_tp_sl_rules() -> None:
    session = make_session()
    strategy = live_strategy()
    strategy["live"]["max_open_positions"] = 3
    live = LiveTradingEngine(strategy, LiveSettings())

    result = stage_manual_buy(
        session,
        TOKEN,
        data_sources=FakeDataSources(600_000),
        live=live,
        now=datetime(2026, 6, 7, 12, 0, 0),
    )

    assert result.ok is True
    assert "Manual BUY staged" in result.message
    assert "Manual Token (MAN)" in result.message
    position = session.scalar(select(LivePosition))
    assert position is not None
    assert position.channel_id.startswith("manual_live_control:")
    assert position.token_address == TOKEN
    assert position.target_profit_pct == 20
    assert position.stop_loss_pct == -30
    order = session.scalar(select(LiveOrder))
    assert order.side == "BUY"
    assert order.reason == "signal_entry"


def test_manual_buy_can_override_entry_size() -> None:
    session = make_session()
    strategy = live_strategy()
    strategy["live"]["max_open_positions"] = 3
    strategy["live"]["max_entry_size_sol"] = 2
    live = LiveTradingEngine(strategy, LiveSettings())

    result = stage_manual_buy(
        session,
        TOKEN,
        data_sources=FakeDataSources(600_000),
        live=live,
        entry_size_sol=1,
        now=datetime(2026, 6, 13, 12, 0, 0),
    )

    assert result.ok is True
    position = session.scalar(select(LivePosition))
    order = session.scalar(select(LiveOrder))
    assert position.entry_size_sol == 1
    assert order.requested_size_sol == 1


def test_manual_buy_rejects_active_position_for_same_token() -> None:
    session = make_session()
    position = add_live_position(session)
    position.token_address = TOKEN
    strategy = live_strategy()
    strategy["live"]["max_open_positions"] = 3

    result = stage_manual_buy(
        session,
        TOKEN,
        data_sources=FakeDataSources(),
        live=LiveTradingEngine(strategy, LiveSettings()),
    )

    assert result.ok is False
    assert "active live position" in result.message


def test_control_bot_parses_manual_buy_token_address() -> None:
    assert _parse_token_address_arg(["/buy", TOKEN], 1) == TOKEN
    assert _parse_token_address_arg(["/buy", f"https://dexscreener.com/solana/{TOKEN}"], 1) == TOKEN


def test_manual_market_cap_triggers_can_be_created_and_cancelled() -> None:
    session = make_session()

    buy = create_manual_buy_trigger(
        session,
        TOKEN,
        target_market_cap_usd=300_000,
        entry_size_sol=0.5,
    )
    sell = create_manual_sell_trigger(
        session,
        TOKEN,
        target_market_cap_usd=500_000,
        sell_ratio=100,
    )

    assert buy.ok is True
    assert sell.ok is True
    triggers = session.scalars(select(ManualLiveTrigger).order_by(ManualLiveTrigger.id)).all()
    assert [(trigger.side, trigger.trigger_direction) for trigger in triggers] == [
        ("BUY", "AT_OR_BELOW"),
        ("SELL", "AT_OR_ABOVE"),
    ]
    assert triggers[0].entry_size_sol == 0.5
    assert triggers[1].sell_ratio == 100

    cancelled = cancel_manual_trigger(session, triggers[0].id)

    assert cancelled.ok is True
    assert triggers[0].status == "CANCELLED"


def test_control_bot_parses_market_cap_suffixes() -> None:
    assert _parse_market_cap_arg(["/buy", TOKEN, "300k"], 2) == 300_000
    assert _parse_market_cap_arg(["/buy", TOKEN, "1.5m"], 2) == 1_500_000
    assert _parse_market_cap_arg(["/buy", TOKEN, "$2,000,000"], 2) == 2_000_000


def test_control_bot_parses_sol_suffixes() -> None:
    assert _parse_sol_arg(["/buy", TOKEN, "300k", "1"], 3) == 1
    assert _parse_sol_arg(["/buy", TOKEN, "300k", "0.5sol"], 3) == 0.5
    assert _looks_like_sol_arg("1sol") is True
    assert _looks_like_sol_arg("3000k") is False


def test_control_bot_creates_conditional_buy_with_default_size(monkeypatch) -> None:
    session = make_session()

    @contextmanager
    def fake_get_session():
        yield session

    monkeypatch.setattr("live.control_bot.get_session", fake_get_session)
    response = LiveControlBot(settings=LiveSettings())._execute_command(f"/buy {TOKEN} 300k")

    trigger = session.scalar(select(ManualLiveTrigger))
    assert "Manual BUY trigger created" in response
    assert trigger is not None
    assert trigger.target_market_cap_usd == 300_000
    assert trigger.entry_size_sol == 0.05


def test_control_bot_creates_conditional_buy_with_sol_suffix(monkeypatch) -> None:
    session = make_session()

    @contextmanager
    def fake_get_session():
        yield session

    monkeypatch.setattr("live.control_bot.get_session", fake_get_session)
    response = LiveControlBot(settings=LiveSettings())._execute_command(
        f"/buy {TOKEN} 300k 0.5sol"
    )

    trigger = session.scalar(select(ManualLiveTrigger))
    assert "Manual BUY trigger created" in response
    assert trigger is not None
    assert trigger.entry_size_sol == 0.5


def test_control_bot_creates_conditional_buy_with_large_k_market_cap(
    monkeypatch,
) -> None:
    session = make_session()

    @contextmanager
    def fake_get_session():
        yield session

    monkeypatch.setattr("live.control_bot.get_session", fake_get_session)
    response = LiveControlBot(settings=LiveSettings())._execute_command(
        f"/buy {TOKEN} 3000k 1sol"
    )

    trigger = session.scalar(select(ManualLiveTrigger))
    assert "Manual BUY trigger created" in response
    assert trigger is not None
    assert trigger.target_market_cap_usd == 3_000_000
    assert trigger.entry_size_sol == 1


def test_control_bot_stages_immediate_buy_with_sol_suffix(monkeypatch) -> None:
    session = make_session()

    @contextmanager
    def fake_get_session():
        yield session

    monkeypatch.setattr("live.control_bot.get_session", fake_get_session)
    monkeypatch.setattr("live.control.DataSourceAggregator", lambda: FakeDataSources(600_000))
    strategy = live_strategy()
    strategy["live"]["max_open_positions"] = 3
    strategy["live"]["max_entry_size_sol"] = 2
    monkeypatch.setattr(
        "live.engine.LiveTradingEngine",
        lambda: LiveTradingEngine(strategy, LiveSettings()),
    )
    response = LiveControlBot(settings=LiveSettings())._execute_command(f"/buy {TOKEN} 1sol")

    position = session.scalar(select(LivePosition))
    order = session.scalar(select(LiveOrder))
    assert "Manual BUY staged" in response
    assert position is not None
    assert position.entry_size_sol == 1
    assert order is not None
    assert order.requested_size_sol == 1


def test_control_bot_balance_reports_disabled_signer() -> None:
    response = get_live_balance(LiveSettings())

    assert response == "Live signer is disabled."


def test_format_live_balance_includes_caps() -> None:
    settings = LiveSettings()
    settings.live_execution_adapter = "signer_service"
    response = _format_live_balance(
        {
            "status": "ready",
            "wallet": TOKEN,
            "balance_sol": 1.234567,
            "fee_reserve_sol": 0.05,
        },
        settings,
    )

    assert "Live wallet balance" in response
    assert "Status: ready" in response
    assert "Wallet: 5s7tf6...Jdpump" in response
    assert "Balance: 1.2346 SOL" in response
    assert "Spendable approx: 1.1846 SOL" in response
    assert "Max entry: 0.05 SOL" in response
