import time
from collections import deque
from threading import Lock

import httpx

from app.settings import get_settings
from data_sources.retry import simple_retry
from data_sources.types import TokenMarketData


class DexScreenerClient:
    _request_times: deque[float] = deque()
    _request_lock = Lock()

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or get_settings().dexscreener_base_url).rstrip("/")
        self.requests_per_minute = get_settings().dexscreener_request_budget_per_minute

    def get_token_market_data(self, token_address: str) -> TokenMarketData | None:
        return self.get_tokens_market_data([token_address]).get(token_address)

    @simple_retry(attempts=2, initial_delay=0.25)
    def resolve_solana_pair_identifier(self, identifier: str) -> str | None:
        self._wait_for_request_budget()
        with httpx.Client(timeout=5) as client:
            response = client.get(f"{self.base_url}/latest/dex/pairs/solana/{identifier}")
            response.raise_for_status()
            raw = response.json() or {}
        pair = _first_pair(raw)
        if not pair or pair.get("chainId") != "solana":
            return None
        return _primary_token_address(pair)

    @simple_retry(attempts=2, initial_delay=0.25)
    def get_tokens_market_data(self, token_addresses: list[str]) -> dict[str, TokenMarketData]:
        unique_addresses = list(dict.fromkeys(token_addresses))
        if not unique_addresses:
            return {}
        if len(unique_addresses) > 30:
            raise ValueError("DexScreener batch token request supports up to 30 token addresses")
        self._wait_for_request_budget()
        with httpx.Client(timeout=5) as client:
            addresses = ",".join(unique_addresses)
            response = client.get(f"{self.base_url}/tokens/v1/solana/{addresses}")
            response.raise_for_status()
            pairs = response.json() or []
        result: dict[str, TokenMarketData] = {}
        for token_address in unique_addresses:
            token_pairs = [
                pair for pair in pairs
                if pair.get("chainId") == "solana" and _contains_token(pair, token_address)
            ]
            if token_pairs:
                pair = max(
                    token_pairs,
                    key=lambda item: ((item.get("liquidity") or {}).get("usd") or 0),
                )
                result[token_address] = _to_market_data(token_address, pair, pairs)
        return result

    def _wait_for_request_budget(self) -> None:
        while True:
            with self._request_lock:
                now = time.monotonic()
                while self._request_times and self._request_times[0] <= now - 60:
                    self._request_times.popleft()
                if len(self._request_times) < self.requests_per_minute:
                    self._request_times.append(now)
                    return
                sleep_seconds = max(self._request_times[0] + 60 - now, 0.01)
            time.sleep(sleep_seconds)


def _contains_token(pair: dict, token_address: str) -> bool:
    return token_address in {
        (pair.get("baseToken") or {}).get("address"),
        (pair.get("quoteToken") or {}).get("address"),
    }


def _first_pair(raw: dict) -> dict | None:
    pairs = raw.get("pairs")
    if isinstance(pairs, list) and pairs and isinstance(pairs[0], dict):
        return pairs[0]
    pair = raw.get("pair")
    if isinstance(pair, dict):
        return pair
    return None


QUOTE_TOKEN_ADDRESSES = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkYk5k7V8w3VK4Qp",
}


def _primary_token_address(pair: dict) -> str | None:
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    base_address = base.get("address")
    quote_address = quote.get("address")
    if base_address and base_address not in QUOTE_TOKEN_ADDRESSES:
        return base_address
    if quote_address and quote_address not in QUOTE_TOKEN_ADDRESSES:
        return quote_address
    return base_address or quote_address


def _to_market_data(token_address: str, pair: dict, raw: list[dict]) -> TokenMarketData:
    token = pair.get("baseToken") or {}
    if token.get("address") != token_address:
        token = pair.get("quoteToken") or {}
    txns_5m = (pair.get("txns") or {}).get("m5") or {}
    return TokenMarketData(
        source="dexscreener",
        token_address=token_address,
        symbol=token.get("symbol"),
        name=token.get("name"),
        price_usd=_to_float(pair.get("priceUsd")),
        fdv_usd=_to_float(pair.get("fdv")),
        market_cap_usd=_to_float(pair.get("marketCap")),
        liquidity_usd=_to_float((pair.get("liquidity") or {}).get("usd")),
        volume_5m_usd=_to_float((pair.get("volume") or {}).get("m5")),
        volume_1h_usd=_to_float((pair.get("volume") or {}).get("h1")),
        volume_6h_usd=_to_float((pair.get("volume") or {}).get("h6")),
        volume_24h_usd=_to_float((pair.get("volume") or {}).get("h24")),
        price_change_5m_pct=_to_float((pair.get("priceChange") or {}).get("m5")),
        price_change_1h_pct=_to_float((pair.get("priceChange") or {}).get("h1")),
        buys_5m=_to_int(txns_5m.get("buys")),
        sells_5m=_to_int(txns_5m.get("sells")),
        pair_address=pair.get("pairAddress"),
        dex_name=pair.get("dexId"),
        raw={"pairs": raw},
    )


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
