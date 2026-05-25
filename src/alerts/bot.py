import logging

import httpx

from app.settings import get_settings

logger = logging.getLogger(__name__)


class TelegramAlertBot:
    def __init__(self):
        self.settings = get_settings()

    def enabled(self) -> bool:
        return bool(self.settings.telegram_alert_bot_token and self.settings.telegram_alert_chat_id)

    def send_message(self, text: str) -> bool:
        if not self.enabled():
            logger.info("Telegram alert skipped because bot token or chat id is not configured.")
            return False

        url = f"https://api.telegram.org/bot{self.settings.telegram_alert_bot_token}/sendMessage"
        payload = {
            "chat_id": self.settings.telegram_alert_chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        with httpx.Client(timeout=10) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        return True


def format_high_score_alert(token_address: str, channel_id: str, score: float) -> str:
    return f"High-score paper signal\nChannel: {channel_id}\nToken: {token_address}\nScore: {score:.1f}"


def format_paper_buy_alert(token_address: str, channel_id: str, size_sol: float, market_cap_usd: float) -> str:
    return (
        "Paper BUY opened\n"
        f"Channel: {channel_id}\n"
        f"Token: {token_address}\n"
        f"Size: {size_sol:.4f} SOL\n"
        f"Entry market cap: ${market_cap_usd:,.0f}"
    )
