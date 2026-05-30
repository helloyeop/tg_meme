import base64
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from app.settings import get_settings
from live.execution import LAMPORTS_PER_SOL, WRAPPED_SOL_MINT

app = FastAPI(title="memetrading isolated signer", docs_url=None, redoc_url=None)


class SwapRequest(BaseModel):
    client_order_id: str = Field(min_length=1, max_length=100)
    side: str
    token_address: str = Field(min_length=32, max_length=64)
    amount: int = Field(gt=0)


class SignerRuntime:
    def __init__(self):
        self.settings = get_settings()
        self.strategy = self.settings.load_strategy_config().get("live", {})
        self.keypair = self._load_keypair(self.settings.live_signer_keypair_path)
        if str(self.keypair.pubkey()) != self.settings.live_wallet_public_key:
            raise RuntimeError("Signer keypair does not match LIVE_WALLET_PUBLIC_KEY.")
        self.ledger_path = Path("/app/signer-data/signer.db")
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_ledger()

    def execute(self, request: SwapRequest) -> dict:
        existing = self._find_result(request.client_order_id)
        if existing:
            return existing
        side = request.side.upper()
        if side not in {"BUY", "SELL"}:
            raise HTTPException(400, "Only BUY and SELL swaps are supported.")
        if side == "BUY":
            self._validate_buy(request.amount)
            input_mint, output_mint = WRAPPED_SOL_MINT, request.token_address
        else:
            self._validate_sell(request.token_address, request.amount)
            input_mint, output_mint = request.token_address, WRAPPED_SOL_MINT

        self._reserve(side, request)
        order = self._get_order(input_mint, output_mint, request.amount)
        signed_transaction = self._sign(order["transaction"])
        execution = self._execute_order(signed_transaction, order["requestId"])
        result = {
            "status": execution.get("status"),
            "signature": execution.get("signature"),
            "request_id": order["requestId"],
            "input_amount": execution.get("inputAmountResult") or order.get("inAmount"),
            "output_amount": execution.get("outputAmountResult") or order.get("outAmount"),
            "error": execution.get("error"),
        }
        self._record(side, request, result)
        return result

    def _validate_buy(self, amount: int) -> None:
        cap = int(float(self.strategy.get("max_entry_size_sol", 0.5)) * LAMPORTS_PER_SOL)
        if amount > cap:
            raise HTTPException(400, "Requested BUY exceeds signer entry cap.")
        daily_limit = int(
            float(self.strategy.get("daily_max_buy_sol", 1)) * LAMPORTS_PER_SOL
        )
        with sqlite3.connect(self.ledger_path) as connection:
            spent = connection.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM swaps
                WHERE side='BUY' AND status='Success' AND trade_day=?
                """,
                (datetime.now(timezone.utc).date().isoformat(),),
            ).fetchone()[0]
        if spent + amount > daily_limit:
            raise HTTPException(400, "Requested BUY exceeds signer daily spend cap.")

    def _validate_sell(self, token_address: str, amount: int) -> None:
        with sqlite3.connect(self.ledger_path) as connection:
            inventory = connection.execute(
                """
                SELECT
                  COALESCE(SUM(CASE WHEN side='BUY' THEN output_amount ELSE 0 END), 0)
                  - COALESCE(SUM(CASE WHEN side='SELL' THEN input_amount ELSE 0 END), 0)
                FROM swaps
                WHERE token_address=? AND status='Success'
                """,
                (token_address,),
            ).fetchone()[0]
        if amount > inventory:
            raise HTTPException(400, "Requested SELL exceeds signer token inventory.")

    def _get_order(self, input_mint: str, output_mint: str, amount: int) -> dict:
        headers = {"x-api-key": self.settings.jupiter_api_key or ""}
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "taker": str(self.keypair.pubkey()),
        }
        with httpx.Client(timeout=15) as client:
            response = client.get(
                f"{self.settings.jupiter_swap_base_url.rstrip('/')}/order",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            order = response.json()
        if (
            order.get("inputMint") != input_mint
            or order.get("outputMint") != output_mint
            or order.get("inAmount") != str(amount)
            or order.get("taker") != str(self.keypair.pubkey())
            or not order.get("transaction")
            or not order.get("requestId")
        ):
            raise HTTPException(502, "Jupiter returned an invalid swap order.")
        return order

    def _sign(self, transaction: str) -> str:
        tx = VersionedTransaction.from_bytes(base64.b64decode(transaction))
        signer_index = list(tx.message.account_keys).index(self.keypair.pubkey())
        signatures = list(tx.signatures)
        signatures[signer_index] = self.keypair.sign_message(to_bytes_versioned(tx.message))
        signed = VersionedTransaction.populate(tx.message, signatures)
        return base64.b64encode(bytes(signed)).decode()

    def _execute_order(self, signed_transaction: str, request_id: str) -> dict:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.settings.jupiter_api_key or "",
        }
        with httpx.Client(timeout=45) as client:
            response = client.post(
                f"{self.settings.jupiter_swap_base_url.rstrip('/')}/execute",
                headers=headers,
                json={"signedTransaction": signed_transaction, "requestId": request_id},
            )
            response.raise_for_status()
            return response.json()

    def _find_result(self, client_order_id: str) -> dict | None:
        with sqlite3.connect(self.ledger_path) as connection:
            row = connection.execute(
                "SELECT status, raw_json FROM swaps WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        if row is None:
            return None
        if row[0] == "PENDING":
            raise HTTPException(409, "Swap request is already pending.")
        return json.loads(row[1])

    def _reserve(self, side: str, request: SwapRequest) -> None:
        with sqlite3.connect(self.ledger_path) as connection:
            connection.execute(
                """
                INSERT INTO swaps(client_order_id, trade_day, side, token_address, amount, status)
                VALUES (?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    request.client_order_id,
                    datetime.now(timezone.utc).date().isoformat(),
                    side,
                    request.token_address,
                    request.amount,
                ),
            )

    def _record(self, side: str, request: SwapRequest, result: dict) -> None:
        with sqlite3.connect(self.ledger_path) as connection:
            connection.execute(
                """
                UPDATE swaps
                SET status=?, signature=?, input_amount=?, output_amount=?, raw_json=?
                WHERE client_order_id=?
                """,
                (
                    result.get("status"),
                    result.get("signature"),
                    int(result.get("input_amount") or request.amount),
                    int(result.get("output_amount") or 0),
                    json.dumps(result),
                    request.client_order_id,
                ),
            )

    def _init_ledger(self) -> None:
        with sqlite3.connect(self.ledger_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS swaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL UNIQUE,
                    trade_day TEXT NOT NULL,
                    side TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT,
                    signature TEXT,
                    input_amount INTEGER,
                    output_amount INTEGER,
                    raw_json TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    @staticmethod
    def _load_keypair(path: Path) -> Keypair:
        if not path.exists():
            raise RuntimeError(f"Signer keypair file is missing: {path}")
        return Keypair.from_bytes(bytes(json.loads(path.read_text(encoding="utf-8"))))


def require_auth(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().live_signer_auth_token
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(401, "Unauthorized")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "wallet": get_settings().live_wallet_public_key}


@app.post("/swap", dependencies=[Depends(require_auth)])
def swap(request: SwapRequest) -> dict:
    try:
        return SignerRuntime().execute(request)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
