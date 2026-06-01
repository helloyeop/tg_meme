import pandas as pd

from dashboard.formatting import (
    format_dashboard_frame,
    format_market_cap_k,
    format_percent,
    format_sol,
)


def test_format_market_cap_k_uses_whole_k_units() -> None:
    assert format_market_cap_k(83_490) == "$83K"
    assert format_market_cap_k(1_250_500) == "$1,250K"
    assert format_market_cap_k(None) == "-"


def test_dashboard_frame_formats_display_values_without_mutating_source() -> None:
    source = pd.DataFrame(
        {
            "entry_market_cap_usd": [83_490.0],
            "market_cap_multiple": [1.234],
            "liquidity_usd": [23_059.83],
            "current_return_pct": [10.42],
            "realized_pnl_sol": [0.1],
            "price_usd": [0.00008459],
        }
    )

    formatted = format_dashboard_frame(source)

    assert formatted.iloc[0].to_dict() == {
        "entry_market_cap_usd": "$83K",
        "market_cap_multiple": "1.23x",
        "liquidity_usd": "$23,060",
        "current_return_pct": "10%",
        "realized_pnl_sol": "0.1",
        "price_usd": 0.00008459,
    }
    assert source.iloc[0]["entry_market_cap_usd"] == 83_490.0


def test_metric_formatters_remove_unnecessary_decimals() -> None:
    assert format_percent(50.0) == "50%"
    assert format_sol(2.0) == "2"
    assert format_sol(0.125) == "0.125"
