import json

from data_sources.gmgn import GMGNClient


def test_gmgn_cli_normalizes_realistic_nested_payload(monkeypatch) -> None:
    def fake_which(path):
        return path

    def fake_run(command, **kwargs):
        class Result:
            returncode = 0
            stderr = ""

            @property
            def stdout(self):
                if command[1:3] == ["token", "info"]:
                    return json.dumps(
                        {
                            "symbol": "ABC",
                            "name": "Token ABC",
                            "price": "0.002",
                            "total_supply": "1000000",
                            "stat": {"price_change_5m": "0.12"},
                        }
                    )
                if command[1:3] == ["token", "pool"]:
                    return json.dumps({"liquidity": "42000", "dex": "raydium"})
                return json.dumps({"list": [{"volume": "710.56"}]})

        return Result()

    monkeypatch.setattr("data_sources.gmgn.shutil.which", fake_which)
    monkeypatch.setattr("data_sources.gmgn.subprocess.run", fake_run)

    data = GMGNClient(cli_path="gmgn-cli").get_token_market_data("token")

    assert data is not None
    assert data.source == "gmgn"
    assert data.symbol == "ABC"
    assert data.market_cap_usd == 2000.0
    assert data.liquidity_usd == 42000.0
    assert data.volume_5m_usd == 710.56


def test_gmgn_returns_none_when_no_cli_or_base_url(monkeypatch) -> None:
    monkeypatch.setattr("data_sources.gmgn.shutil.which", lambda path: None)

    assert GMGNClient(base_url="", cli_path="gmgn-cli").get_token_market_data("token") is None


def test_gmgn_wallet_flow_data_summarizes_smart_and_kol_trades(monkeypatch) -> None:
    def fake_which(path):
        return path

    def fake_run(command, **kwargs):
        class Result:
            returncode = 0
            stderr = ""

            @property
            def stdout(self):
                args = command[1:]
                if args[:2] == ["token", "traders"] and "smart_degen" in args:
                    if "buy_volume_cur" in args:
                        return json.dumps(
                            {
                                "list": [
                                    {
                                        "address": "smart-1",
                                        "buy_volume_cur": "2500",
                                        "sell_volume_cur": "100",
                                    },
                                    {
                                        "address": "smart-2",
                                        "buy_volume_cur": "1500",
                                        "sell_volume_cur": "0",
                                    },
                                ]
                            }
                        )
                    return json.dumps(
                        {
                            "list": [
                                {
                                    "address": "smart-1",
                                    "sell_volume_cur": "100",
                                }
                            ]
                        }
                    )
                if args[:2] == ["token", "traders"] and "renowned" in args:
                    return json.dumps(
                        {
                            "list": [
                                {
                                    "address": "kol-1",
                                    "buy_volume_cur": "500",
                                    "sell_volume_cur": "0",
                                }
                            ]
                        }
                    )
                if args[:2] == ["track", "smartmoney"] and "buy" in args:
                    return json.dumps(
                        {
                            "list": [
                                {
                                    "maker": "smart-3",
                                    "base_address": "token",
                                    "amount_usd": "1000",
                                },
                                {
                                    "maker": "smart-4",
                                    "base_address": "other",
                                    "amount_usd": "9999",
                                },
                            ]
                        }
                    )
                if args[:2] == ["track", "smartmoney"] and "sell" in args:
                    return json.dumps({"list": []})
                if args[:2] == ["track", "kol"] and "buy" in args:
                    return json.dumps(
                        {
                            "list": [
                                {
                                    "maker": "kol-2",
                                    "base_address": "token",
                                    "amount_usd": "700",
                                }
                            ]
                        }
                    )
                return json.dumps({"list": []})

        return Result()

    monkeypatch.setattr("data_sources.gmgn.shutil.which", fake_which)
    monkeypatch.setattr("data_sources.gmgn.subprocess.run", fake_run)

    data = GMGNClient(cli_path="gmgn-cli").get_token_wallet_flow_data("token")

    assert data is not None
    assert data.smart_trader_count == 2
    assert data.smart_recent_buy_count == 1
    assert data.smart_net_buy_usd == 4900
    assert data.kol_trader_count == 1
    assert data.kol_recent_buy_count == 1
    assert data.confidence_score >= 35
