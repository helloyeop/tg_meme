from dataclasses import dataclass

import httpx

from app.settings import get_settings

WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"


class LiveExecutionDisabled(RuntimeError):
    pass


@dataclass
class JupiterOrder:
    request_id: str | None
    transaction: str | None
    input_mint: str
    output_mint: str
    in_amount: str
    out_amount: str | None
    raw: dict


class JupiterSwapClient:
    """Readies Jupiter Swap V2 orders without signing or submitting them."""

    def __init__(self):
        self.settings = get_settings()

    def get_order(
        self,
        *,
        input_mint: str,
        output_mint: str,
        amount: int,
        taker: str | None = None,
    ) -> JupiterOrder:
        headers = {}
        if self.settings.jupiter_api_key:
            headers["x-api-key"] = self.settings.jupiter_api_key
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
        }
        if taker:
            params["taker"] = taker
        with httpx.Client(timeout=10) as client:
            response = client.get(
                f"{self.settings.jupiter_swap_base_url.rstrip('/')}/order",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        return JupiterOrder(
            request_id=payload.get("requestId"),
            transaction=payload.get("transaction"),
            input_mint=payload.get("inputMint", input_mint),
            output_mint=payload.get("outputMint", output_mint),
            in_amount=payload.get("inAmount", str(amount)),
            out_amount=payload.get("outAmount"),
            raw=payload,
        )

    def execute_signed_order(self, *, signed_transaction: str, request_id: str) -> dict:
        if self.settings.live_execution_adapter == "disabled":
            raise LiveExecutionDisabled("Live execution adapter is disabled.")
        raise LiveExecutionDisabled(
            "No signer-backed live execution adapter is configured. "
            "The app intentionally refuses to submit transactions."
        )
