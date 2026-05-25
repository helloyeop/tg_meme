import logging

from data_sources.dexscreener import DexScreenerClient
from data_sources.gmgn import GMGNClient
from data_sources.helius import HeliusClient
from data_sources.types import TokenMarketData, TokenSecurityData

logger = logging.getLogger(__name__)


class DataSourceAggregator:
    def __init__(
        self,
        gmgn: GMGNClient | None = None,
        dexscreener: DexScreenerClient | None = None,
        helius: HeliusClient | None = None,
    ):
        self.gmgn = gmgn or GMGNClient()
        self.dexscreener = dexscreener or DexScreenerClient()
        self.helius = helius or HeliusClient()

    def get_market_data(self, token_address: str) -> TokenMarketData | None:
        partial_gmgn_data: TokenMarketData | None = None
        for name, client in (("gmgn", self.gmgn), ("dexscreener", self.dexscreener)):
            try:
                data = client.get_token_market_data(token_address)
            except Exception as exc:
                logger.warning("%s market data failed for %s: %s", name, token_address, exc)
                continue
            if data and data.market_cap_usd is not None:
                return data
            if name == "gmgn" and data and data.liquidity_usd is not None:
                partial_gmgn_data = data
        return partial_gmgn_data

    def get_security_data(self, token_address: str) -> TokenSecurityData | None:
        try:
            data = self.gmgn.get_token_security_data(token_address)
            if data:
                return data
        except Exception as exc:
            logger.warning("gmgn security data failed for %s: %s", token_address, exc)
        try:
            return self.helius.get_token_security_data(token_address)
        except Exception as exc:
            logger.warning("helius security data failed for %s: %s", token_address, exc)
            return None
