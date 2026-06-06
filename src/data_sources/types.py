from dataclasses import dataclass, field


@dataclass
class TokenMarketData:
    source: str
    token_address: str
    symbol: str | None = None
    name: str | None = None
    price_usd: float | None = None
    fdv_usd: float | None = None
    market_cap_usd: float | None = None
    liquidity_usd: float | None = None
    volume_5m_usd: float | None = None
    volume_1h_usd: float | None = None
    volume_6h_usd: float | None = None
    volume_24h_usd: float | None = None
    price_change_5m_pct: float | None = None
    price_change_1h_pct: float | None = None
    buys_5m: int | None = None
    sells_5m: int | None = None
    makers_5m: int | None = None
    pair_address: str | None = None
    dex_name: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class TokenSecurityData:
    source: str
    token_address: str
    holder_count: int | None = None
    top10_holder_ratio: float | None = None
    dev_wallet: str | None = None
    dev_wallet_ratio: float | None = None
    mint_authority_active: bool | None = None
    freeze_authority_active: bool | None = None
    liquidity_locked: bool | None = None
    risk_flags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class TokenWalletFlowData:
    source: str
    token_address: str
    smart_trader_count: int = 0
    smart_net_buy_usd: float | None = None
    smart_buy_volume_usd: float | None = None
    smart_sell_volume_usd: float | None = None
    smart_recent_buy_count: int = 0
    smart_recent_sell_count: int = 0
    kol_trader_count: int = 0
    kol_net_buy_usd: float | None = None
    kol_buy_volume_usd: float | None = None
    kol_sell_volume_usd: float | None = None
    kol_recent_buy_count: int = 0
    kol_recent_sell_count: int = 0
    top_trader_sell_pressure_usd: float | None = None
    confidence_score: float = 0
    raw: dict = field(default_factory=dict)
