from pathlib import Path

import yaml


def test_strategy_example_keeps_v1_paper_risk_limits():
    strategy = yaml.safe_load(Path("config/strategy.example.yaml").read_text(encoding="utf-8"))

    assert strategy["paper"]["entry_size_sol"] == 0.5
    assert strategy["paper"]["daily_max_loss_sol"] == 2
    assert strategy["entry"]["final_signal_score_min"] == 5
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
    assert strategy["live"]["require_entry_round_trip_quote"] is True
    assert strategy["live"]["min_entry_round_trip_recovery_pct"] == 90
    assert strategy["live"]["take_profit_retry_seconds"] == 30
    assert strategy["live"]["stop_loss_pct"] == -70
    assert strategy["live"]["stop_loss_by_entry_market_cap"] == {
        "below_500k_pct": -35,
        "from_500k_to_below_1m_pct": -30,
        "from_1m_to_below_5m_pct": -25,
        "at_or_above_5m_pct": -20,
    }
    assert strategy["live"]["daily_max_loss_sol"] == 1
    assert strategy["live"]["daily_max_buy_sol"] == 1
    assert strategy["live"]["entry_setup"] == {
        "enabled": True,
        "observation_seconds": 600,
        "pullback_pct": -20,
        "reclaim_pct": 8,
        "expire_without_entry": True,
        "fresh_momentum": {
            "max_market_cap_usd": 1000000,
            "observation_seconds": 600,
            "pullback_pct": -20,
            "reclaim_pct": 8,
        },
        "established_pullback": {
            "enabled": True,
            "min_market_cap_usd": 1000000,
            "observation_seconds": 86400,
            "pullback_pct": -20,
            "require_reclaim": False,
        },
        "gmgn_confirmation": {
            "enabled": True,
            "allow_missing_activity_data": True,
            "allow_missing_security_data": True,
            "require_buy_pressure": False,
            "min_buy_sell_ratio": 1.1,
            "min_buys_5m": 1,
            "min_makers_5m": 5,
            "max_top10_holder_ratio": 45,
            "max_dev_wallet_ratio": 5,
            "block_mint_authority_active": True,
            "block_freeze_authority_active": True,
            "block_risk_flags": True,
        },
        "smart_money_confirmation": {
            "enabled": True,
            "allow_missing_wallet_flow_data": True,
            "min_confidence_score": 35,
            "min_smart_trader_count": 1,
            "min_smart_net_buy_usd": 0,
            "min_recent_smart_buy_count": 0,
            "allow_kol_as_support": True,
            "min_kol_trader_count": 1,
            "min_kol_net_buy_usd": 0,
            "block_on_smart_recent_sell_count": 2,
            "block_on_negative_smart_net_buy": True,
        },
    }
    assert strategy["actionable_recall"]["min_minutes_since_previous_actionable"] == 60
    assert strategy["actionable_recall"]["entry_size_factor"] == 0.50
    assert strategy["actionable_recall"]["chase_risk_factor"]["up_150_to_300_pct"] == 0.70
