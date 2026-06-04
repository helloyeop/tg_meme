import json
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from alerts.bot import TelegramAlertBot, format_token_label, format_usd, short_token_address
from app.settings import get_settings
from db.models import LiveOrder, LivePosition, TokenMarketSnapshot

WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000
logger = logging.getLogger(__name__)


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


class SignerClient:
    """Calls the isolated signer service without exposing its keypair to this app."""

    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    def execute(
        self,
        *,
        client_order_id: str,
        side: str,
        token_address: str,
        amount: int,
        min_output_amount: int | None = None,
    ) -> dict:
        if self.settings.live_execution_adapter != "signer_service":
            raise LiveExecutionDisabled("Signer service execution is disabled.")
        if not self.settings.live_signer_auth_token:
            raise LiveExecutionDisabled("LIVE_SIGNER_AUTH_TOKEN is not configured.")
        with httpx.Client(timeout=45) as client:
            response = client.post(
                f"{self.settings.live_signer_base_url.rstrip('/')}/swap",
                headers={"Authorization": f"Bearer {self.settings.live_signer_auth_token}"},
                json={
                    "client_order_id": client_order_id,
                    "side": side,
                    "token_address": token_address,
                    "amount": amount,
                    "min_output_amount": min_output_amount,
                },
            )
            response.raise_for_status()
            return response.json()

    def quote_sell(self, *, token_address: str, amount: int) -> dict:
        return self._quote(
            "/quote",
            {"side": "SELL", "token_address": token_address, "amount": amount},
        )

    def quote_buy_round_trip(self, *, token_address: str, amount: int) -> dict:
        return self._quote(
            "/quote/buy-round-trip",
            {"side": "BUY", "token_address": token_address, "amount": amount},
        )

    def _quote(self, path: str, payload: dict) -> dict:
        if self.settings.live_execution_adapter != "signer_service":
            raise LiveExecutionDisabled("Signer service quoting is disabled.")
        if not self.settings.live_signer_auth_token:
            raise LiveExecutionDisabled("LIVE_SIGNER_AUTH_TOKEN is not configured.")
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.settings.live_signer_base_url.rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {self.settings.live_signer_auth_token}"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()


class LiveOrderExecutor:
    """Executes staged orders through the isolated signer and updates the live ledger."""

    def __init__(self, settings=None, signer=None, alerts=None):
        self.settings = settings or get_settings()
        self.signer = signer or SignerClient(self.settings)
        self.alerts = alerts or TelegramAlertBot()

    def execute_staged_orders(self, session: Session, limit: int = 5) -> int:
        if self.settings.live_execution_adapter != "signer_service":
            return 0
        orders = session.scalars(
            select(LiveOrder)
            .where(LiveOrder.status == "STAGED")
            .order_by(LiveOrder.requested_at.asc(), LiveOrder.id.asc())
            .limit(limit)
        ).all()
        executed = 0
        for order in orders:
            position = session.get(LivePosition, order.position_id)
            if position is None:
                order.status = "FAILED"
                order.raw_json = json.dumps({"error": "missing_live_position"})
                continue
            amount = self._amount_for_order(order, position)
            if amount is None:
                order.status = "FAILED"
                order.raw_json = json.dumps({"error": "missing_swap_amount"})
                continue
            try:
                payload = self.signer.execute(
                    client_order_id=f"live-order-{order.id}",
                    side=order.side,
                    token_address=order.token_address,
                    amount=amount,
                    min_output_amount=self._min_output_amount(order, position),
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409 and order.side == "SELL":
                    order.status = "FAILED"
                    order.raw_json = json.dumps({"error": str(exc)})
                    position.status = "OPEN"
                    position.exit_requested_time = None
                    self._send_failure_alert(session, order, position, str(exc))
                elif exc.response.status_code == 400:
                    order.status = "FAILED"
                    order.raw_json = json.dumps({"error": str(exc)})
                    if order.side == "BUY":
                        position.status = "ENTRY_FAILED"
                    elif order.side == "SELL":
                        position.status = "OPEN"
                        position.exit_requested_time = None
                    self._send_failure_alert(session, order, position, str(exc))
                else:
                    order.status = "STAGED"
                    order.raw_json = json.dumps({"error": str(exc)})
                continue
            except Exception as exc:
                order.status = "STAGED"
                order.raw_json = json.dumps({"error": str(exc)})
                continue

            order.raw_json = json.dumps(payload)
            order.jupiter_request_id = payload.get("request_id")
            order.transaction_signature = payload.get("signature")
            if payload.get("status") != "Success":
                order.status = "FAILED"
                if order.side == "SELL":
                    position.status = "OPEN"
                    position.exit_requested_time = None
                elif order.side == "BUY":
                    position.status = "ENTRY_FAILED"
                self._send_failure_alert(
                    session,
                    order,
                    position,
                    str(payload.get("error") or "Signer returned non-success status."),
                )
                continue

            order.status = "CONFIRMED"
            if order.side == "BUY":
                position.status = "OPEN"
                position.entry_input_lamports = str(amount)
                position.token_amount_raw = str(payload.get("output_amount") or "")
                position.entry_wallet_delta_lamports = str(
                    payload.get("wallet_balance_delta_lamports") or ""
                )
            else:
                position.status = "CLOSED"
                position.exit_confirmed_time = order.requested_at
                position.exit_output_lamports = str(payload.get("output_amount") or "")
                position.exit_wallet_delta_lamports = str(
                    payload.get("wallet_balance_delta_lamports") or ""
                )
                position.realized_pnl_sol = self._realized_pnl_sol(position)
            self._send_alert(session, order, position)
            executed += 1
        return executed

    def _amount_for_order(self, order: LiveOrder, position: LivePosition) -> int | None:
        if order.side == "BUY" and order.requested_size_sol is not None:
            return int(order.requested_size_sol * LAMPORTS_PER_SOL)
        if order.side == "SELL" and position.token_amount_raw:
            return int(position.token_amount_raw)
        return None

    def _min_output_amount(self, order: LiveOrder, position: LivePosition) -> int | None:
        if order.side != "SELL" or not order.reason.startswith("take_profit"):
            return None
        if not position.entry_input_lamports:
            return None
        return int(int(position.entry_input_lamports) * (1 + position.target_profit_pct / 100))

    def _realized_pnl_sol(self, position: LivePosition) -> float:
        if position.entry_wallet_delta_lamports and position.exit_wallet_delta_lamports:
            return (
                int(position.entry_wallet_delta_lamports) + int(position.exit_wallet_delta_lamports)
            ) / LAMPORTS_PER_SOL
        return (
            int(position.exit_output_lamports or 0) - int(position.entry_input_lamports or 0)
        ) / LAMPORTS_PER_SOL

    def _send_alert(self, session: Session, order: LiveOrder, position: LivePosition) -> None:
        symbol, name = self._latest_token_identity(session, position.token_address)
        label = format_token_label(position.token_address, symbol=symbol, name=name)
        size_line = (
            f"Size: {position.entry_size_sol:g} SOL"
            if order.side == "BUY"
            else f"PnL: {position.realized_pnl_sol:.4f} SOL"
        )
        try:
            self.alerts.send_message(
                "\n".join(
                    [
                        f"Live {order.side} confirmed",
                        f"Token: {label}",
                        f"CA: {short_token_address(order.token_address)}",
                        f"Reason: {order.reason}",
                        size_line,
                        f"Entry MC: {format_usd(position.entry_market_cap_usd)}",
                        f"Target MC: {format_usd(position.target_market_cap_usd)}",
                        f"Signature: {order.transaction_signature}",
                        f"Position: {position.status}",
                    ]
                )
            )
        except Exception:
            logger.exception("Failed to send live trade Telegram alert")

    def _send_failure_alert(
        self,
        session: Session,
        order: LiveOrder,
        position: LivePosition,
        error: str,
    ) -> None:
        symbol, name = self._latest_token_identity(session, position.token_address)
        label = format_token_label(position.token_address, symbol=symbol, name=name)
        trimmed_error = error if len(error) <= 450 else f"{error[:450]}..."
        try:
            self.alerts.send_message(
                "\n".join(
                    [
                        f"Live {order.side} failed",
                        f"Token: {label}",
                        f"CA: {short_token_address(order.token_address)}",
                        f"Reason: {order.reason}",
                        f"Order: #{order.id}",
                        f"Position: {position.status}",
                        f"Error: {trimmed_error}",
                    ]
                )
            )
        except Exception:
            logger.exception("Failed to send live trade failure Telegram alert")

    @staticmethod
    def _latest_token_identity(
        session: Session,
        token_address: str,
    ) -> tuple[str | None, str | None]:
        snapshot = session.scalars(
            select(TokenMarketSnapshot)
            .where(TokenMarketSnapshot.token_address == token_address)
            .order_by(TokenMarketSnapshot.snapshot_time.desc(), TokenMarketSnapshot.id.desc())
            .limit(1)
        ).first()
        if snapshot is None:
            return None, None
        return snapshot.symbol, snapshot.name
