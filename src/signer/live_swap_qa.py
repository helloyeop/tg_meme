import argparse
import json
import uuid

from signer.quote_qa import USDC_MINT
from signer.service import SignerRuntime, SwapRequest

QA_BUY_LAMPORTS = 10_000_000


def sanitized(result: dict) -> dict:
    return {
        "status": result.get("status"),
        "signature": result.get("signature"),
        "request_id": result.get("request_id"),
        "input_amount": result.get("input_amount"),
        "output_amount": result.get("output_amount"),
        "error": result.get("error"),
    }


def run_live_swap_qa(confirm: bool) -> dict:
    if not confirm:
        raise RuntimeError("Refusing live swap QA without --confirm-live-swap.")
    runtime = SignerRuntime()
    qa_id = f"manual-live-qa-{uuid.uuid4()}"
    before = runtime.sol_balance_lamports()
    buy = runtime.execute(
        SwapRequest(
            client_order_id=f"{qa_id}-buy",
            side="BUY",
            token_address=USDC_MINT,
            amount=QA_BUY_LAMPORTS,
        )
    )
    if buy.get("status") != "Success" or not buy.get("output_amount"):
        return {"status": "BUY_FAILED", "balance_before_lamports": before, "buy": sanitized(buy)}
    sell = runtime.execute(
        SwapRequest(
            client_order_id=f"{qa_id}-sell",
            side="SELL",
            token_address=USDC_MINT,
            amount=int(buy["output_amount"]),
        )
    )
    after = runtime.sol_balance_lamports()
    return {
        "status": "Success" if sell.get("status") == "Success" else "SELL_FAILED",
        "balance_before_lamports": before,
        "balance_after_lamports": after,
        "net_lamports": after - before,
        "buy": sanitized(buy),
        "sell": sanitized(sell),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live-swap", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_live_swap_qa(args.confirm_live_swap), indent=2))
