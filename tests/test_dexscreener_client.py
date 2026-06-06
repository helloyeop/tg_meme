from data_sources.dexscreener import DexScreenerClient


def test_batch_market_data_uses_official_multi_token_endpoint_and_liquid_pair(monkeypatch) -> None:
    requested_urls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return [
                {
                    "chainId": "solana",
                    "pairAddress": "small",
                    "baseToken": {"address": "mint-a", "symbol": "A"},
                    "quoteToken": {"address": "sol"},
                    "marketCap": 100,
                    "liquidity": {"usd": 10},
                },
                {
                    "chainId": "solana",
                    "pairAddress": "large",
                    "baseToken": {"address": "mint-a", "symbol": "A"},
                    "quoteToken": {"address": "sol"},
                    "marketCap": 250,
                    "liquidity": {"usd": 500},
                },
                {
                    "chainId": "solana",
                    "pairAddress": "b",
                    "baseToken": {"address": "mint-b", "symbol": "B"},
                    "quoteToken": {"address": "sol"},
                    "marketCap": 300,
                    "liquidity": {"usd": 200},
                },
            ]

    class FakeClient:
        def __init__(self, timeout: int):
            assert timeout == 5

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url: str) -> FakeResponse:
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr("data_sources.dexscreener.httpx.Client", FakeClient)
    DexScreenerClient._request_times.clear()
    client = DexScreenerClient(base_url="https://api.dexscreener.com")

    data = client.get_tokens_market_data(["mint-a", "mint-b"])

    assert requested_urls == ["https://api.dexscreener.com/tokens/v1/solana/mint-a,mint-b"]
    assert data["mint-a"].pair_address == "large"
    assert data["mint-a"].market_cap_usd == 250
    assert data["mint-b"].market_cap_usd == 300


def test_batch_market_data_rejects_more_than_documented_token_limit() -> None:
    client = DexScreenerClient(base_url="https://api.dexscreener.com")

    try:
        client.get_tokens_market_data([f"mint-{index}" for index in range(31)])
    except ValueError as exc:
        assert "up to 30" in str(exc)
    else:
        raise AssertionError("Expected the documented 30-token maximum to be enforced")


def test_resolves_dexscreener_pair_identifier_to_base_token(monkeypatch) -> None:
    requested_urls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "pair": {
                    "chainId": "solana",
                    "pairAddress": "pair",
                    "baseToken": {"address": "token", "symbol": "MEME"},
                    "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
                }
            }

    class FakeClient:
        def __init__(self, timeout: int):
            assert timeout == 5

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url: str) -> FakeResponse:
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr("data_sources.dexscreener.httpx.Client", FakeClient)
    DexScreenerClient._request_times.clear()
    client = DexScreenerClient(base_url="https://api.dexscreener.com")

    assert client.resolve_solana_pair_identifier("pair") == "token"
    assert requested_urls == ["https://api.dexscreener.com/latest/dex/pairs/solana/pair"]
