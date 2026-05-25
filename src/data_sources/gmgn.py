import json
import os
import shutil
import subprocess

import httpx

from app.settings import get_settings
from data_sources.retry import simple_retry
from data_sources.types import TokenMarketData, TokenSecurityData


class GMGNCommandError(RuntimeError):
    pass


class GMGNClient:
    def __init__(self, base_url: str | None = None, cli_path: str | None = None, timeout_seconds: int = 20):
        settings = get_settings()
        self.base_url = (base_url or settings.gmgn_base_url or "").rstrip("/")
        self.api_key = settings.gmgn_api_key
        self.cli_path = cli_path if cli_path is not None else settings.gmgn_cli_path or "./node_modules/.bin/gmgn-cli"
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
            "security": self._run_cli_json("token", "security", "--chain", "sol", "--address", token_address, "--raw"),
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
            dev_wallet=_first_str(security.get("creator"), security.get("creator_address"), security.get("dev_wallet")),
            dev_wallet_ratio=_ratio_to_pct(
                _first_number(security.get("creator_hold_rate"), security.get("dev_wallet_ratio"))
            ),
            mint_authority_active=_to_bool(security.get("mint_authority_active")),
            freeze_authority_active=_to_bool(security.get("freeze_authority_active")),
            liquidity_locked=_to_bool(security.get("liquidity_locked")),
            risk_flags=[str(flag) for flag in _first_list(security.get("risk_flags"), security.get("flags"))],
            raw={**raw, "holder_sample_size": len(holder_items)},
        )

    def _get_token_market_data_from_cli(self, token_address: str) -> TokenMarketData:
        raw = {
            "token": self._run_cli_json("token", "info", "--chain", "sol", "--address", token_address, "--raw"),
        }
        try:
            raw["pool"] = self._run_cli_json("token", "pool", "--chain", "sol", "--address", token_address, "--raw")
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
            fdv_usd=_first_number(token.get("fdv"), token.get("market_cap"), token.get("market_cap_usd")),
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
            price_change_5m_pct=_ratio_to_pct(_first_number(market.get("price_change_5m"), stat.get("price_change_5m"))),
            price_change_1h_pct=_ratio_to_pct(_first_number(market.get("price_change_1h"), stat.get("price_change_1h"))),
            buys_5m=_first_int(market.get("buys_5m"), stat.get("buys_5m")),
            sells_5m=_first_int(market.get("sells_5m"), stat.get("sells_5m")),
            makers_5m=_first_int(market.get("makers_5m"), stat.get("makers_5m")),
            pair_address=_first_str(pool.get("pair_address"), pool.get("address"), token.get("pair_address")),
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
    supply = _first_number(token.get("circulating_supply"), token.get("total_supply"), token.get("max_supply"))
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
