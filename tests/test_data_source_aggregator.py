from data_sources.aggregator import DataSourceAggregator
from data_sources.types import TokenMarketData


class StubClient:
    def __init__(self, data):
        self.data = data

    def get_token_market_data(self, token_address: str):
        return self.data


def test_dexscreener_is_used_when_gmgn_market_data_has_no_market_cap() -> None:
    gmgn = StubClient(TokenMarketData(source="gmgn", token_address="token", liquidity_usd=42000))
    dex = StubClient(TokenMarketData(source="dexscreener", token_address="token", market_cap_usd=2000))

    data = DataSourceAggregator(gmgn=gmgn, dexscreener=dex).get_market_data("token")

    assert data is not None
    assert data.source == "dexscreener"
    assert data.market_cap_usd == 2000


def test_partial_gmgn_data_is_retained_when_fallback_has_no_usable_market_cap() -> None:
    gmgn = StubClient(TokenMarketData(source="gmgn", token_address="token", liquidity_usd=42000))
    dex = StubClient(None)

    data = DataSourceAggregator(gmgn=gmgn, dexscreener=dex).get_market_data("token")

    assert data is not None
    assert data.source == "gmgn"
    assert data.liquidity_usd == 42000
