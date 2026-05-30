from pathlib import Path

import pytest
from fastapi import HTTPException

from signer.service import SignerRuntime, SwapRequest


def runtime_with_ledger(path: Path) -> SignerRuntime:
    runtime = SignerRuntime.__new__(SignerRuntime)
    runtime.strategy = {
        "max_entry_size_sol": 0.5,
        "daily_max_buy_sol": 1,
    }
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

    assert runtime._find_result("live-order-1")["signature"] == "signature"


def test_signer_rejects_sell_above_recorded_inventory(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")

    with pytest.raises(HTTPException, match="inventory"):
        runtime._validate_sell("token-address", 1)


def test_signer_rejects_buy_above_entry_cap(tmp_path: Path) -> None:
    runtime = runtime_with_ledger(tmp_path / "signer.db")

    with pytest.raises(HTTPException, match="entry cap"):
        runtime._validate_buy(500_000_001)
