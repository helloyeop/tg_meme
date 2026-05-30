import json

import httpx

from app.settings import get_settings
from live.execution import LAMPORTS_PER_SOL, WRAPPED_SOL_MINT

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def run_quote_qa() -> dict:
    settings = get_settings()
    if not settings.jupiter_api_key:
        raise RuntimeError("JUPITER_API_KEY is not configured.")
    with httpx.Client(timeout=15) as client:
        response = client.get(
            f"{settings.jupiter_swap_base_url.rstrip('/')}/order",
            headers={"x-api-key": settings.jupiter_api_key},
            params={
                "inputMint": WRAPPED_SOL_MINT,
                "outputMint": USDC_MINT,
                "amount": str(int(0.5 * LAMPORTS_PER_SOL)),
            },
        )
        response.raise_for_status()
        payload = response.json()
    result = {
        "authenticated": True,
        "input_mint": payload.get("inputMint"),
        "output_mint": payload.get("outputMint"),
        "input_amount": payload.get("inAmount"),
        "output_amount": payload.get("outAmount"),
        "swap_type": payload.get("swapType"),
        "router": payload.get("router"),
        "transaction_present": bool(payload.get("transaction")),
        "request_id_present": bool(payload.get("requestId")),
        "error_code": payload.get("errorCode"),
        "error_message": payload.get("errorMessage") or payload.get("error"),
    }
    if result["transaction_present"]:
        raise RuntimeError("Quote-only QA unexpectedly returned a transaction.")
    return result


if __name__ == "__main__":
    print(json.dumps(run_quote_qa(), indent=2))
