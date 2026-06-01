from pathlib import Path

import yaml


def test_strategy_example_keeps_v1_paper_risk_limits():
    strategy = yaml.safe_load(Path("config/strategy.example.yaml").read_text(encoding="utf-8"))

    assert strategy["paper"]["entry_size_sol"] == 0.5
    assert strategy["paper"]["daily_max_loss_sol"] == 2
    assert strategy["entry"]["final_signal_score_min"] == 45
    assert strategy["entry"]["risk_score_min"] == 60
    assert strategy["entry"]["min_liquidity_usd"] == 1000
    assert strategy["exit"]["stop_loss_pct"] == -50
    assert strategy["live"]["entry_size_sol"] == 0.5
    assert strategy["live"]["take_profit_pct"] == 10
    assert strategy["live"]["take_profit_by_entry_market_cap"] == {
        "below_500k_pct": 30,
        "from_500k_to_below_1m_pct": 20,
        "at_or_above_1m_pct": 10,
    }
    assert strategy["live"]["take_profit_retry_seconds"] == 30
    assert strategy["live"]["stop_loss_pct"] == -70
    assert strategy["live"]["daily_max_loss_sol"] == 1
    assert strategy["live"]["daily_max_buy_sol"] == 1
    assert strategy["actionable_recall"]["min_minutes_since_previous_actionable"] == 60
    assert strategy["actionable_recall"]["entry_size_factor"] == 0.50
    assert strategy["actionable_recall"]["chase_risk_factor"]["up_150_to_300_pct"] == 0.70
