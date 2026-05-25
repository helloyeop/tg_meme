import httpx

from app.settings import get_settings
from data_sources.retry import simple_retry
from data_sources.types import TokenSecurityData


class HeliusClient:
    def __init__(self, rpc_url: str | None = None):
        settings = get_settings()
        self.rpc_url = rpc_url or settings.helius_rpc_url or settings.solana_rpc_url
        self.api_key = settings.helius_api_key

    @simple_retry(attempts=2, initial_delay=0.25)
    def get_token_security_data(self, token_address: str) -> TokenSecurityData | None:
        if not self.rpc_url:
            return None
        payload = {
            "jsonrpc": "2.0",
            "id": "memetrading",
            "method": "getTokenLargestAccounts",
            "params": [token_address],
        }
        with httpx.Client(timeout=5) as client:
            response = client.post(self.rpc_url, json=payload)
            response.raise_for_status()
            raw = response.json()
        accounts = ((raw.get("result") or {}).get("value")) or []
        holder_count = len(accounts) if accounts else None
        return TokenSecurityData(
            source="helius",
            token_address=token_address,
            holder_count=holder_count,
            raw=raw,
        )
