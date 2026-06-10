import logging
import time
from dataclasses import dataclass

import httpx

from alerts.bot import short_token_address
from app.settings import get_settings
from db.session import get_session, init_db
from live.control import (
    cancel_manual_trigger,
    create_manual_buy_trigger,
    create_manual_sell_trigger,
    get_position_detail,
    list_live_positions,
    list_manual_triggers,
    set_live_entry_paused,
    stage_manual_buy,
    stage_manual_sell,
    update_stop_loss,
    update_take_profit,
)
from telegram.ca_extractor import (
    extract_dexscreener_solana_identifiers,
    extract_solana_addresses,
    is_valid_solana_address,
)

logger = logging.getLogger(__name__)

HELP_TEXT = """
Live control commands
/live - list active live positions
/pos <id> - show live position details
/balance - show live wallet SOL balance
/pause_live - pause new live entries
/resume_live - resume new live entries
/buy <CA> - stage a manual BUY using the same live TP/SL rules
/buy <CA> <marketcap> [SOL] - watch and BUY at or below market cap, e.g. /buy CA 300k 0.5sol
/sell <id> - stage a full manual SELL
/sell <CA> <marketcap> [all] - watch and SELL at or above market cap
/triggers - list watching manual market-cap triggers
/cancel_trigger <id> - cancel a manual market-cap trigger
/tp <id> <pct> - set take-profit percent, e.g. /tp 5 20
/sl <id> <pct> - set stop-loss percent, e.g. /sl 5 -70
""".strip()


@dataclass
class TelegramUpdate:
    update_id: int
    chat_id: str
    text: str


class LiveControlBot:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.base_url = (
            f"https://api.telegram.org/bot{self.settings.telegram_alert_bot_token}"
            if self.settings.telegram_alert_bot_token
            else None
        )
        self.allowed_chat_id = str(self.settings.telegram_alert_chat_id or "")

    def run(self, poll_seconds: int = 2) -> None:
        if not self.base_url or not self.allowed_chat_id:
            raise RuntimeError("Telegram alert bot token and chat id are required.")
        logger.info("Starting live control bot for chat_id=%s", self.allowed_chat_id)
        offset = self._initial_offset()
        while True:
            try:
                updates = self._get_updates(offset)
                for update in updates:
                    offset = max(offset, update.update_id + 1)
                    self._handle_update(update)
            except Exception:
                logger.exception("Live control bot polling failed")
                time.sleep(max(poll_seconds, 5))
            else:
                time.sleep(poll_seconds)

    def _get_updates(self, offset: int) -> list[TelegramUpdate]:
        assert self.base_url is not None
        with httpx.Client(timeout=35) as client:
            response = client.post(
                f"{self.base_url}/getUpdates",
                json={
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                },
            )
            response.raise_for_status()
            payload = response.json()
        updates: list[TelegramUpdate] = []
        for item in payload.get("result", []):
            message = item.get("message") or {}
            text = (message.get("text") or "").strip()
            chat_id = str((message.get("chat") or {}).get("id") or "")
            if text:
                updates.append(TelegramUpdate(item["update_id"], chat_id, text))
        return updates

    def _initial_offset(self) -> int:
        assert self.base_url is not None
        with httpx.Client(timeout=10) as client:
            response = client.post(
                f"{self.base_url}/getUpdates",
                json={"timeout": 0, "allowed_updates": ["message"]},
            )
            response.raise_for_status()
            payload = response.json()
        update_ids = [item["update_id"] for item in payload.get("result", [])]
        return max(update_ids, default=-1) + 1

    def _handle_update(self, update: TelegramUpdate) -> None:
        if update.chat_id != self.allowed_chat_id:
            logger.warning("Ignoring unauthorized live control chat_id=%s", update.chat_id)
            self._send(update.chat_id, "Unauthorized.")
            return
        response = self._execute_command(update.text)
        self._send(update.chat_id, response)

    def _execute_command(self, text: str) -> str:
        parts = text.split()
        command = parts[0].split("@", 1)[0].lower() if parts else ""
        try:
            if command in {"/start", "/help"}:
                return HELP_TEXT
            if command == "/live":
                with get_session() as session:
                    return list_live_positions(session)
            if command == "/pos":
                position_id = _parse_int_arg(parts, 1, "position id")
                with get_session() as session:
                    return get_position_detail(session, position_id).message
            if command == "/balance":
                return get_live_balance(self.settings)
            if command == "/pause_live":
                with get_session() as session:
                    set_live_entry_paused(session, True, "telegram_control_bot")
                    session.commit()
                return "New live entries paused. Existing positions can still be sold."
            if command == "/resume_live":
                with get_session() as session:
                    set_live_entry_paused(session, False, "telegram_control_bot")
                    session.commit()
                return "New live entries resumed."
            if command == "/buy":
                token_address = _parse_token_address_arg(parts, 1)
                with get_session() as session:
                    if len(parts) >= 3:
                        result = create_manual_buy_trigger(
                            session,
                            token_address,
                            target_market_cap_usd=_parse_market_cap_arg(parts, 2),
                            entry_size_sol=(
                                _parse_sol_arg(parts, 3)
                                if len(parts) >= 4
                                else _default_live_entry_size_sol(self.settings)
                            ),
                        )
                    else:
                        result = stage_manual_buy(session, token_address)
                    if result.ok:
                        session.commit()
                    else:
                        session.rollback()
                    return result.message
            if command == "/sell":
                if len(parts) >= 3 and not _looks_like_int(parts[1]):
                    token_address = _parse_token_address_arg(parts, 1)
                    sell_ratio = _parse_sell_ratio_arg(parts, 3) if len(parts) >= 4 else 100
                    with get_session() as session:
                        result = create_manual_sell_trigger(
                            session,
                            token_address,
                            target_market_cap_usd=_parse_market_cap_arg(parts, 2),
                            sell_ratio=sell_ratio,
                        )
                        if result.ok:
                            session.commit()
                        else:
                            session.rollback()
                        return result.message
                position_id = _parse_int_arg(parts, 1, "position id")
                with get_session() as session:
                    result = stage_manual_sell(session, position_id)
                    if result.ok:
                        session.commit()
                    else:
                        session.rollback()
                    return result.message
            if command == "/triggers":
                with get_session() as session:
                    return list_manual_triggers(session)
            if command == "/cancel_trigger":
                trigger_id = _parse_int_arg(parts, 1, "trigger id")
                with get_session() as session:
                    result = cancel_manual_trigger(session, trigger_id)
                    if result.ok:
                        session.commit()
                    else:
                        session.rollback()
                    return result.message
            if command == "/tp":
                position_id = _parse_int_arg(parts, 1, "position id")
                pct = _parse_float_arg(parts, 2, "take-profit percent")
                with get_session() as session:
                    result = update_take_profit(session, position_id, pct)
                    if result.ok:
                        session.commit()
                    else:
                        session.rollback()
                    return result.message
            if command == "/sl":
                position_id = _parse_int_arg(parts, 1, "position id")
                pct = _parse_float_arg(parts, 2, "stop-loss percent")
                with get_session() as session:
                    result = update_stop_loss(session, position_id, pct)
                    if result.ok:
                        session.commit()
                    else:
                        session.rollback()
                    return result.message
            return f"Unknown command.\n\n{HELP_TEXT}"
        except ValueError as exc:
            return f"{exc}\n\n{HELP_TEXT}"

    def _send(self, chat_id: str, text: str) -> None:
        assert self.base_url is not None
        with httpx.Client(timeout=10) as client:
            response = client.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()


def _parse_int_arg(parts: list[str], index: int, label: str) -> int:
    if len(parts) <= index:
        raise ValueError(f"Missing {label}.")
    try:
        return int(parts[index])
    except ValueError as exc:
        raise ValueError(f"Invalid {label}: {parts[index]}") from exc


def _parse_float_arg(parts: list[str], index: int, label: str) -> float:
    if len(parts) <= index:
        raise ValueError(f"Missing {label}.")
    try:
        return float(parts[index])
    except ValueError as exc:
        raise ValueError(f"Invalid {label}: {parts[index]}") from exc


def _parse_sol_arg(parts: list[str], index: int) -> float:
    if len(parts) <= index:
        raise ValueError("Missing entry size SOL.")
    raw = parts[index].strip().lower()
    if raw.endswith("sol"):
        raw = raw[:-3].strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid entry size SOL: {parts[index]}") from exc
    if value <= 0:
        raise ValueError("Entry size SOL must be positive.")
    return value


def _default_live_entry_size_sol(settings) -> float:
    live = settings.load_strategy_config().get("live", {})
    return float(live.get("entry_size_sol", 0.5))


def _parse_market_cap_arg(parts: list[str], index: int) -> float:
    if len(parts) <= index:
        raise ValueError("Missing market cap.")
    raw = parts[index].strip().lower().replace("$", "").replace(",", "")
    multiplier = 1.0
    if raw.endswith("k"):
        multiplier = 1_000
        raw = raw[:-1]
    elif raw.endswith("m"):
        multiplier = 1_000_000
        raw = raw[:-1]
    elif raw.endswith("b"):
        multiplier = 1_000_000_000
        raw = raw[:-1]
    try:
        value = float(raw) * multiplier
    except ValueError as exc:
        raise ValueError(f"Invalid market cap: {parts[index]}") from exc
    if value <= 0:
        raise ValueError("Market cap must be positive.")
    return value


def _parse_sell_ratio_arg(parts: list[str], index: int) -> float:
    raw = parts[index].strip().lower().rstrip("%")
    if raw == "all":
        return 100
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid sell ratio: {parts[index]}") from exc
    if value <= 0 or value > 100:
        raise ValueError("Sell ratio must be between 0 and 100.")
    return value


def _parse_token_address_arg(parts: list[str], index: int) -> str:
    if len(parts) <= index:
        raise ValueError("Missing Solana token address.")
    raw = " ".join(parts[index:])
    if is_valid_solana_address(parts[index]):
        return parts[index]
    addresses = extract_solana_addresses(raw)
    if addresses:
        return addresses[0]
    for identifier in extract_dexscreener_solana_identifiers(raw):
        if is_valid_solana_address(identifier):
            return identifier
    raise ValueError(f"Invalid Solana token address: {parts[index]}")


def _looks_like_int(value: str) -> bool:
    try:
        int(value)
    except ValueError:
        return False
    return True


def get_live_balance(settings) -> str:
    if settings.live_execution_adapter != "signer_service":
        return "Live signer is disabled."
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{settings.live_signer_base_url.rstrip('/')}/readiness")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return f"Live balance unavailable: {exc}"
    return _format_live_balance(payload, settings)


def _format_live_balance(payload: dict, settings) -> str:
    balance_sol = float(payload.get("balance_sol") or 0)
    fee_reserve_sol = float(payload.get("fee_reserve_sol") or settings.live_fee_reserve_sol)
    spendable_sol = max(balance_sol - fee_reserve_sol, 0)
    live = settings.load_strategy_config().get("live", {})
    lines = [
        "Live wallet balance",
        f"Status: {payload.get('status') or 'unknown'}",
        f"Wallet: {short_token_address(str(payload.get('wallet') or 'unknown'))}",
        f"Balance: {balance_sol:.4f} SOL",
        f"Fee reserve: {fee_reserve_sol:.4f} SOL",
        f"Spendable approx: {spendable_sol:.4f} SOL",
        f"Default entry: {float(live.get('entry_size_sol', 0)):.4g} SOL",
        f"Max entry: {float(live.get('max_entry_size_sol', 0)):.4g} SOL",
        f"Daily buy cap: {float(live.get('daily_max_buy_sol', 0)):.4g} SOL",
    ]
    return "\n".join(lines)


def run_live_control_bot() -> None:
    init_db()
    LiveControlBot().run()
