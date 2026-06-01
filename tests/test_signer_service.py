from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from signer.service import QuoteRequest, SignerRuntime, SwapRequest


def runtime_with_ledger(path: Path) -> SignerRuntime:
    runtime = SignerRuntime.__new__(SignerRuntime)
    runtime.strategy = {
        "max_entry_size_sol": 0.5,
        "daily_max_buy_sol": 1,
    }
    runtime.settings = SimpleNamespace(live_fee_reserve_sol=0.05)
    runtime.sol_balance_lamports = lambda: 2_000_000_000
    runtime.ledger_path = path
    runtime._init_ledger()
    return runtime


def test_signer_replays_completed_idempotent_result(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")
    request = SwapRequest(
        client_order_id="live-order-1",
        side="BUY",
        token_address="So11111111111111111111111111111111111111112",
        amount=500_000_000,
    )
    runtime._reserve("BUY", request)
    runtime._record(
        "BUY",
        request,
        {
            "status": "Success",
            "signature": "signature",
            "input_amount": "500000000",
            "output_amount": "123",
        },
    )

    assert runtime._find_existing(request)["raw_json"].find("signature") > 0


def test_signer_rejects_sell_above_recorded_inventory(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")

    with pytest.raises(HTTPException, match="inventory"):
        runtime._validate_sell("token-address", 1)


def test_signer_rejects_buy_above_entry_cap(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")

    with pytest.raises(HTTPException, match="entry cap"):
        runtime._validate_buy(500_000_001)


def test_signer_rejects_buy_when_balance_cannot_cover_fee_reserve(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")
    runtime.sol_balance_lamports = lambda: 549_999_999

    with pytest.raises(HTTPException, match="fee reserve"):
        runtime._validate_buy(500_000_000)


def test_signer_resumes_pending_submission_with_same_request_id(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")
    request = SwapRequest(
        client_order_id="live-order-resume",
        side="BUY",
        token_address="So11111111111111111111111111111111111111112",
        amount=10_000_000,
    )
    runtime._reserve("BUY", request)
    runtime._store_pending_execution("live-order-resume", "request-id", "signed")
    runtime._execute_order = lambda signed_transaction, request_id: {
        "status": "Success",
        "signature": f"{signed_transaction}:{request_id}",
        "inputAmountResult": "10000000",
        "outputAmountResult": "123",
    }

    result = runtime.execute(request)

    assert result["status"] == "Success"
    assert result["signature"] == "signed:request-id"


def test_signer_ledger_migrates_minimum_output_column(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")

    import sqlite3

    with sqlite3.connect(runtime.ledger_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(swaps)").fetchall()
        }

    assert "min_output_amount" in columns


def test_signer_round_trip_quote_reports_executable_recovery(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")
    orders = iter(
        [
            {
                "inputMint": "sol",
                "outputMint": "token",
                "inAmount": "500000000",
                "outAmount": "1000",
                "priceImpact": "0.01",
                "slippageBps": 50,
                "feeBps": 10,
                "routePlan": [],
            },
            {
                "inputMint": "token",
                "outputMint": "sol",
                "inAmount": "1000",
                "outAmount": "450000000",
                "priceImpact": "0.02",
                "slippageBps": 50,
                "feeBps": 10,
                "routePlan": [],
            },
        ]
    )
    runtime._get_order = lambda *_: next(orders)

    quote = runtime.quote_buy_round_trip(
        QuoteRequest(
            side="BUY",
            token_address="So11111111111111111111111111111111111111112",
            amount=500_000_000,
        )
    )

    assert quote["recovery_pct"] == 90
    assert quote["buy"]["out_amount"] == "1000"
    assert quote["sell"]["out_amount"] == "450000000"
