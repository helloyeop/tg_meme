import json
import os
import shutil
import subprocess

import httpx

from app.settings import get_settings
from data_sources.retry import simple_retry
from data_sources.types import TokenMarketData, TokenSecurityData, TokenWalletFlowData


class GMGNCommandError(RuntimeError):
    pass


class GMGNClient:
    def __init__(
        self,
        base_url: str | None = None,
        cli_path: str | None = None,
        timeout_seconds: int = 20,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.gmgn_base_url or "").rstrip("/")
        self.api_key = settings.gmgn_api_key
        self.cli_path = (
            cli_path
            if cli_path is not None
            else settings.gmgn_cli_path or "./node_modules/.bin/gmgn-cli"
        )
        self.timeout_seconds = timeout_seconds

    @simple_retry(attempts=2, initial_delay=0.25)
    def get_token_market_data(self, token_address: str) -> TokenMarketData | None:
        if self._cli_available():
            return self._get_token_market_data_from_cli(token_address)
        if not self.base_url:
            return None
        return self._get_token_market_data_from_rest(token_address)

    @simple_retry(attempts=2, initial_delay=0.25)
    def get_token_security_data(self, token_address: str) -> TokenSecurityData | None:
        if not self._cli_available():
            return None
        raw = {
            "security": self._run_cli_json(
                "token",
                "security",
                "--chain",
                "sol",
                "--address",
                token_address,
                "--raw",
            ),
        }
        try:
            raw["holders"] = self._run_cli_json(
                "token",
                "holders",
                "--chain",
                "sol",
                "--address",
                token_address,
                "--limit",
                "50",
                "--raw",
            )
        except GMGNCommandError as exc:
            raw["holders_error"] = str(exc)

        security = raw.get("security") or {}
        holders = raw.get("holders") or {}
        holder_items = _first_list(holders.get("list"), holders.get("holders"), holders.get("data"))
        return TokenSecurityData(
            source="gmgn",
            token_address=token_address,
            holder_count=_first_int(
                security.get("holder_count"),
                security.get("holders"),
                holders.get("total"),
                holders.get("holder_count"),
            ),
            top10_holder_ratio=_ratio_to_pct(
                _first_number(
                    security.get("top10_holder_rate"),
                    security.get("top_10_holder_rate"),
                    security.get("top10_holder_ratio"),
                )
            ),
            dev_wallet=_first_str(
                security.get("creator"),
                security.get("creator_address"),
                security.get("dev_wallet"),
            ),
            dev_wallet_ratio=_ratio_to_pct(
                _first_number(security.get("creator_hold_rate"), security.get("dev_wallet_ratio"))
            ),
            mint_authority_active=_to_bool(security.get("mint_authority_active")),
            freeze_authority_active=_to_bool(security.get("freeze_authority_active")),
            liquidity_locked=_to_bool(security.get("liquidity_locked")),
            risk_flags=[
                str(flag)
                for flag in _first_list(security.get("risk_flags"), security.get("flags"))
            ],
            raw={**raw, "holder_sample_size": len(holder_items)},
        )

    @simple_retry(attempts=2, initial_delay=0.25)
    def get_token_wallet_flow_data(self, token_address: str) -> TokenWalletFlowData | None:
        if not self._cli_available():
            return None
        raw: dict[str, dict] = {}
        for key, args in {
            "smart_buy_traders": (
                "token",
                "traders",
                "--chain",
                "sol",
                "--address",
                token_address,
                "--tag",
                "smart_degen",
                "--order-by",
                "buy_volume_cur",
                "--direction",
                "desc",
                "--limit",
                "20",
                "--raw",
            ),
            "smart_sell_traders": (
                "token",
                "traders",
                "--chain",
                "sol",
                "--address",
                token_address,
                "--tag",
                "smart_degen",
                "--order-by",
                "sell_volume_cur",
                "--direction",
                "desc",
                "--limit",
                "20",
                "--raw",
            ),
            "kol_traders": (
                "token",
                "traders",
                "--chain",
                "sol",
                "--address",
                token_address,
                "--tag",
                "renowned",
                "--order-by",
                "buy_volume_cur",
                "--direction",
                "desc",
                "--limit",
                "20",
                "--raw",
            ),
            "recent_smart_buys": (
                "track",
                "smartmoney",
                "--chain",
                "sol",
                "--side",
                "buy",
                "--limit",
                "200",
                "--raw",
            ),
            "recent_smart_sells": (
                "track",
                "smartmoney",
                "--chain",
                "sol",
                "--side",
                "sell",
                "--limit",
                "200",
                "--raw",
            ),
            "recent_kol_buys": (
                "track",
                "kol",
                "--chain",
                "sol",
                "--side",
                "buy",
                "--limit",
                "200",
                "--raw",
            ),
            "recent_kol_sells": (
                "track",
                "kol",
                "--chain",
                "sol",
                "--side",
                "sell",
                "--limit",
                "200",
                "--raw",
            ),
        }.items():
            try:
                raw[key] = self._run_cli_json(*args)
            except GMGNCommandError as exc:
                raw[f"{key}_error"] = {"error": str(exc)}

        smart_buy_traders = _first_list(
            raw.get("smart_buy_traders", {}).get("list"),
            raw.get("smart_buy_traders", {}).get("data"),
        )
        smart_sell_traders = _first_list(
            raw.get("smart_sell_traders", {}).get("list"),
            raw.get("smart_sell_traders", {}).get("data"),
        )
        kol_traders = _first_list(
            raw.get("kol_traders", {}).get("list"),
            raw.get("kol_traders", {}).get("data"),
        )
        recent_smart_buys = _matching_recent_trades(raw.get("recent_smart_buys"), token_address)
        recent_smart_sells = _matching_recent_trades(raw.get("recent_smart_sells"), token_address)
        recent_kol_buys = _matching_recent_trades(raw.get("recent_kol_buys"), token_address)
        recent_kol_sells = _matching_recent_trades(raw.get("recent_kol_sells"), token_address)

        smart_buy_volume = _sum_field(smart_buy_traders, "buy_volume_cur")
        smart_sell_volume = _sum_field(smart_sell_traders, "sell_volume_cur")
        kol_buy_volume = _sum_field(kol_traders, "buy_volume_cur")
        kol_sell_volume = _sum_field(kol_traders, "sell_volume_cur")
        smart_recent_buy_usd = _sum_field(recent_smart_buys, "amount_usd")
        smart_recent_sell_usd = _sum_field(recent_smart_sells, "amount_usd")
        kol_recent_buy_usd = _sum_field(recent_kol_buys, "amount_usd")
        kol_recent_sell_usd = _sum_field(recent_kol_sells, "amount_usd")
        smart_net_buy = (
            smart_buy_volume
            + smart_recent_buy_usd
            - smart_sell_volume
            - smart_recent_sell_usd
        )
        kol_net_buy = kol_buy_volume + kol_recent_buy_usd - kol_sell_volume - kol_recent_sell_usd
        confidence_score = _wallet_flow_confidence_score(
            smart_trader_count=_distinct_wallet_count(smart_buy_traders),
            smart_net_buy_usd=smart_net_buy,
            smart_recent_buy_count=_distinct_wallet_count(recent_smart_buys),
            smart_recent_sell_count=_distinct_wallet_count(recent_smart_sells),
            kol_trader_count=_distinct_wallet_count(kol_traders),
            kol_net_buy_usd=kol_net_buy,
            kol_recent_buy_count=_distinct_wallet_count(recent_kol_buys),
            kol_recent_sell_count=_distinct_wallet_count(recent_kol_sells),
        )
        return TokenWalletFlowData(
            source="gmgn",
            token_address=token_address,
            smart_trader_count=_distinct_wallet_count(smart_buy_traders),
            smart_net_buy_usd=smart_net_buy,
            smart_buy_volume_usd=smart_buy_volume + smart_recent_buy_usd,
            smart_sell_volume_usd=smart_sell_volume + smart_recent_sell_usd,
            smart_recent_buy_count=_distinct_wallet_count(recent_smart_buys),
            smart_recent_sell_count=_distinct_wallet_count(recent_smart_sells),
            kol_trader_count=_distinct_wallet_count(kol_traders),
            kol_net_buy_usd=kol_net_buy,
            kol_buy_volume_usd=kol_buy_volume + kol_recent_buy_usd,
            kol_sell_volume_usd=kol_sell_volume + kol_recent_sell_usd,
            kol_recent_buy_count=_distinct_wallet_count(recent_kol_buys),
            kol_recent_sell_count=_distinct_wallet_count(recent_kol_sells),
            top_trader_sell_pressure_usd=smart_sell_volume + kol_sell_volume,
            confidence_score=confidence_score,
            raw={
                **raw,
                "matched_recent": {
                    "smart_buys": recent_smart_buys,
                    "smart_sells": recent_smart_sells,
                    "kol_buys": recent_kol_buys,
                    "kol_sells": recent_kol_sells,
                },
            },
        )

    def _get_token_market_data_from_cli(self, token_address: str) -> TokenMarketData:
        raw = {
            "token": self._run_cli_json(
                "token",
                "info",
                "--chain",
                "sol",
                "--address",
                token_address,
                "--raw",
            ),
        }
        try:
            raw["pool"] = self._run_cli_json(
                "token",
                "pool",
                "--chain",
                "sol",
                "--address",
                token_address,
                "--raw",
            )
        except GMGNCommandError as exc:
            raw["pool_error"] = str(exc)
        try:
            raw["market"] = self._run_cli_json(
                "market",
                "kline",
                "--chain",
                "sol",
                "--address",
                token_address,
                "--resolution",
                "5m",
                "--raw",
            )
        except GMGNCommandError as exc:
            raw["market_error"] = str(exc)

        token = raw.get("token") or {}
        pool = raw.get("pool") or {}
        market = raw.get("market") or {}
        stat = token.get("stat") if isinstance(token.get("stat"), dict) else {}
        latest_candle = _latest_candle(market)
        return TokenMarketData(
            source="gmgn",
            token_address=token_address,
            symbol=_first_str(token.get("symbol"), raw.get("symbol")),
            name=_first_str(token.get("name"), raw.get("name")),
            price_usd=_first_number(token.get("price"), market.get("price"), raw.get("price_usd")),
            fdv_usd=_first_number(
                token.get("fdv"),
                token.get("market_cap"),
                token.get("market_cap_usd"),
            ),
            market_cap_usd=_computed_market_cap(token),
            liquidity_usd=_first_number(
                market.get("liquidity_usd"),
                pool.get("liquidity_usd"),
                pool.get("liquidity"),
                token.get("liquidity"),
            ),
            volume_5m_usd=_first_number(
                latest_candle.get("volume") if latest_candle else None,
                market.get("volume_5m"),
                stat.get("volume_5m"),
            ),
            volume_1h_usd=_first_number(market.get("volume_1h"), stat.get("volume_1h")),
            price_change_5m_pct=_ratio_to_pct(
                _first_number(market.get("price_change_5m"), stat.get("price_change_5m"))
            ),
            price_change_1h_pct=_ratio_to_pct(
                _first_number(market.get("price_change_1h"), stat.get("price_change_1h"))
            ),
            buys_5m=_first_int(market.get("buys_5m"), stat.get("buys_5m")),
            sells_5m=_first_int(market.get("sells_5m"), stat.get("sells_5m")),
            makers_5m=_first_int(market.get("makers_5m"), stat.get("makers_5m")),
            pair_address=_first_str(
                pool.get("pair_address"),
                pool.get("address"),
                token.get("pair_address"),
            ),
            dex_name=_first_str(pool.get("dex"), pool.get("dex_name"), token.get("dex")),
            raw=raw,
        )

    def _get_token_market_data_from_rest(self, token_address: str) -> TokenMarketData | None:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        with httpx.Client(timeout=10, headers=headers) as client:
            response = client.get(f"{self.base_url}/tokens/sol/{token_address}")
            response.raise_for_status()
            raw = response.json()
        data = raw.get("data", raw)
        return TokenMarketData(
            source="gmgn",
            token_address=token_address,
            symbol=data.get("symbol"),
            name=data.get("name"),
            price_usd=_to_float(data.get("price") or data.get("price_usd")),
            fdv_usd=_to_float(data.get("fdv") or data.get("fdv_usd")),
            market_cap_usd=_to_float(data.get("market_cap") or data.get("market_cap_usd")),
            liquidity_usd=_to_float(data.get("liquidity") or data.get("liquidity_usd")),
            volume_5m_usd=_to_float(data.get("volume_5m")),
            volume_1h_usd=_to_float(data.get("volume_1h")),
            price_change_5m_pct=_to_float(data.get("price_change_5m")),
            price_change_1h_pct=_to_float(data.get("price_change_1h")),
            buys_5m=_to_int(data.get("buys_5m")),
            sells_5m=_to_int(data.get("sells_5m")),
            makers_5m=_to_int(data.get("makers_5m")),
            pair_address=data.get("pair_address"),
            dex_name=data.get("dex") or data.get("dex_name"),
            raw=raw,
        )

    def _cli_available(self) -> bool:
        return bool(self.cli_path and shutil.which(self.cli_path))

    def _run_cli_json(self, *args: str) -> dict:
        if not self.cli_path:
            raise GMGNCommandError("GMGN CLI path is not configured")
        env = os.environ.copy()
        if self.api_key:
            env["GMGN_API_KEY"] = self.api_key
        result = subprocess.run(
            [self.cli_path, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=min(self.timeout_seconds, 8),
            env=env,
        )
        if result.returncode != 0:
            raise GMGNCommandError(result.stderr.strip() or "GMGN CLI command failed")
        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GMGNCommandError(f"GMGN CLI did not return JSON: {result.stdout[:300]}") from exc
        if not isinstance(raw, dict):
            raise GMGNCommandError("GMGN CLI returned non-object JSON")
        return raw


def _to_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _first_number(*values) -> float | None:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _first_int(*values) -> int | None:
    for value in values:
        parsed = _to_int(value)
        if parsed is not None:
            return parsed
    return None


def _first_str(*values) -> str | None:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return None


def _first_list(*values) -> list:
    for value in values:
        if isinstance(value, list):
            return value
    return []


def _sum_field(items: list, field: str) -> float:
    total = 0.0
    for item in items:
        if isinstance(item, dict):
            total += _to_float(item.get(field)) or 0
    return total


def _wallet_address(item: dict) -> str | None:
    maker_info = item.get("maker_info") if isinstance(item.get("maker_info"), dict) else {}
    return _first_str(
        item.get("maker"),
        item.get("address"),
        item.get("wallet"),
        maker_info.get("address"),
    )


def _distinct_wallet_count(items: list) -> int:
    wallets = {
        wallet
        for item in items
        if isinstance(item, dict) and (wallet := _wallet_address(item))
    }
    return len(wallets) if wallets else len([item for item in items if isinstance(item, dict)])


def _matching_recent_trades(raw: dict | None, token_address: str) -> list[dict]:
    if not isinstance(raw, dict):
        return []
    items = _first_list(raw.get("list"), raw.get("data"))
    return [
        item
        for item in items
        if isinstance(item, dict) and item.get("base_address") == token_address
    ]


def _wallet_flow_confidence_score(
    *,
    smart_trader_count: int,
    smart_net_buy_usd: float,
    smart_recent_buy_count: int,
    smart_recent_sell_count: int,
    kol_trader_count: int,
    kol_net_buy_usd: float,
    kol_recent_buy_count: int,
    kol_recent_sell_count: int,
) -> float:
    score = 0.0
    score += min(smart_trader_count, 5) * 12
    score += min(smart_recent_buy_count, 5) * 16
    score += min(kol_trader_count, 3) * 6
    score += min(kol_recent_buy_count, 3) * 8
    if smart_net_buy_usd > 0:
        score += min(smart_net_buy_usd / 1_000, 25)
    if kol_net_buy_usd > 0:
        score += min(kol_net_buy_usd / 1_000, 10)
    score -= min(smart_recent_sell_count, 5) * 18
    score -= min(kol_recent_sell_count, 3) * 8
    if smart_net_buy_usd < 0:
        score += max(smart_net_buy_usd / 1_000, -30)
    if kol_net_buy_usd < 0:
        score += max(kol_net_buy_usd / 1_000, -10)
    return max(0.0, min(100.0, score))


def _latest_candle(market: dict) -> dict | None:
    candles = market.get("list")
    if isinstance(candles, list) and candles and isinstance(candles[-1], dict):
        return candles[-1]
    return None


def _computed_market_cap(token: dict) -> float | None:
    explicit = _first_number(
        token.get("market_cap"),
        token.get("market_cap_usd"),
        token.get("fdv"),
        token.get("circulating_market_cap"),
    )
    if explicit is not None:
        return explicit
    price = _to_float(token.get("price"))
    supply = _first_number(
        token.get("circulating_supply"),
        token.get("total_supply"),
        token.get("max_supply"),
    )
    if price is None or supply is None:
        return None
    return price * supply


def _ratio_to_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100 if 0 <= value <= 1 else value


def _to_bool(value) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"true", "yes", "1", "active", "enabled"}:
        return True
    if lowered in {"false", "no", "0", "inactive", "disabled"}:
        return False
    return None
