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
    assert strategy["live"]["entry_size_sol"] == 0.05
    assert strategy["live"]["take_profit_pct"] == 10
