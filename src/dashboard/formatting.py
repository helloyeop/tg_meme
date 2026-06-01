from typing import Any

import pandas as pd


def _is_missing(value: Any) -> bool:
    return value is None or bool(pd.isna(value))


def _format_number(value: Any, decimals: int) -> str:
    if _is_missing(value):
        return "-"
    return f"{float(value):,.{decimals}f}".rstrip("0").rstrip(".")


def format_market_cap_k(value: Any) -> str:
    if _is_missing(value):
        return "-"
    return f"${float(value) / 1_000:,.0f}K"


def format_usd(value: Any) -> str:
    if _is_missing(value):
        return "-"
    return f"${float(value):,.0f}"


def format_percent(value: Any) -> str:
    if _is_missing(value):
        return "-"
    return f"{float(value):,.0f}%"


def format_sol(value: Any) -> str:
    return _format_number(value, decimals=4)


def format_multiple(value: Any) -> str:
    formatted = _format_number(value, decimals=2)
    return formatted if formatted == "-" else f"{formatted}x"


def format_dashboard_frame(frame: pd.DataFrame) -> pd.DataFrame:
    formatted = frame.copy()
    for column in formatted.columns:
        if column.endswith("_multiple"):
            formatted[column] = formatted[column].map(format_multiple)
        elif "market_cap" in column or column == "fdv_usd":
            formatted[column] = formatted[column].map(format_market_cap_k)
        elif column == "price_usd":
            continue
        elif column.endswith("_usd"):
            formatted[column] = formatted[column].map(format_usd)
        elif column.endswith("_pct"):
            formatted[column] = formatted[column].map(format_percent)
        elif column.endswith("_sol"):
            formatted[column] = formatted[column].map(format_sol)
        elif column.endswith("_score") or column.endswith("_confidence"):
            formatted[column] = formatted[column].map(lambda value: _format_number(value, 2))
    return formatted
