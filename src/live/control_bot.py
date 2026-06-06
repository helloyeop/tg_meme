import logging
import time
from dataclasses import dataclass

import httpx

from app.settings import get_settings
from db.session import get_session, init_db
from live.control import (
    get_position_detail,
    list_live_positions,
    set_live_entry_paused,
    stage_manual_sell,
    update_stop_loss,
    update_take_profit,
)

logger = logging.getLogger(__name__)

HELP_TEXT = """
Live control commands
/live - list active live positions
/pos <id> - show live position details
/pause_live - pause new live entries
/resume_live - resume new live entries
/sell <id> - stage a full manual SELL
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
            if command == "/sell":
                position_id = _parse_int_arg(parts, 1, "position id")
                with get_session() as session:
                    result = stage_manual_sell(session, position_id)
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


def run_live_control_bot() -> None:
    init_db()
    LiveControlBot().run()
