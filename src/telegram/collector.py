import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.settings import get_settings
from db.repositories import log_app_error, upsert_channel_alias, upsert_message
from db.session import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class ChannelConfig:
    name: str
    identifier: str
    enabled: bool = True
    private: bool = False


def load_channel_configs() -> list[ChannelConfig]:
    config = get_settings().load_channels_config()
    return [
        ChannelConfig(
            name=item.get("name") or item.get("identifier"),
            identifier=item["identifier"],
            enabled=item.get("enabled", True),
            private=item.get("private", False),
        )
        for item in config.get("channels", [])
        if item.get("identifier")
    ]


def enabled_unique_channels() -> list[ChannelConfig]:
    channels: list[ChannelConfig] = []
    configured_identifiers: set[str] = set()
    for channel in load_channel_configs():
        if not channel.enabled:
            continue
        identifier_key = channel.identifier.lower().rstrip("/")
        if identifier_key in configured_identifiers:
            logger.warning(
                "Duplicate Telegram channel identifier ignored: %s (name=%s)",
                channel.identifier,
                channel.name,
            )
            continue
        configured_identifiers.add(identifier_key)
        channels.append(channel)
    return channels


class TelegramCollector:
    def __init__(self, session_factory=SessionLocal):
        self.settings = get_settings()
        self.session_factory = session_factory

    async def run(self) -> None:
        if not self.settings.telegram_api_id or not self.settings.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required for collector mode.")

        from telethon import TelegramClient, events

        channels = enabled_unique_channels()
        if not channels:
            raise RuntimeError("No enabled Telegram channels found in config/channels.yaml.")
        client = TelegramClient(
            self._session_path(),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
        )

        await client.start()
        name_by_chat_id = await self._sync_channel_aliases(client, channels)

        @client.on(events.NewMessage(chats=[channel.identifier for channel in channels]))
        async def on_new_message(event) -> None:
            await self._store_event_message(event, name_by_chat_id)

        @client.on(events.MessageEdited(chats=[channel.identifier for channel in channels]))
        async def on_edited_message(event) -> None:
            await self._store_event_message(event, name_by_chat_id)

        logger.info("Starting Telethon collector for %s channels.", len(channels))
        await client.run_until_disconnected()

    async def collect_history_once(self, limit_per_channel: int = 50) -> int:
        if not self.settings.telegram_api_id or not self.settings.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required for history collection.")

        from telethon import TelegramClient

        channels = enabled_unique_channels()
        if not channels:
            raise RuntimeError("No enabled Telegram channels found in config/channels.yaml.")
        count = 0
        async with TelegramClient(
            self._session_path(),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
        ) as client:
            await self._sync_channel_aliases(client, channels)
            for channel in channels:
                async for message in client.iter_messages(channel.identifier, limit=limit_per_channel):
                    with self.session_factory() as session:
                        self._store_message(session, message, channel.name)
                        session.commit()
                        count += 1
        return count

    async def _store_event_message(self, event, name_by_chat_id: dict[str, str]) -> None:
        with self.session_factory() as session:
            try:
                channel_id = name_by_chat_id.get(str(event.chat_id))
                if not channel_id:
                    raise RuntimeError(f"Unmapped configured Telegram chat id: {event.chat_id}")
                self._store_message(session, event.message, channel_id)
                session.commit()
            except Exception as exc:
                log_app_error(session, "telegram_collector", exc, {"chat_id": getattr(event, "chat_id", None)})
                session.commit()

    async def _sync_channel_aliases(self, client, channels: list[ChannelConfig]) -> dict[str, str]:
        from telethon import utils

        name_by_chat_id: dict[str, str] = {}
        with self.session_factory() as session:
            for channel in channels:
                entity = await client.get_entity(channel.identifier)
                numeric_channel_id = str(utils.get_peer_id(entity))
                username = getattr(entity, "username", None)
                name_by_chat_id[numeric_channel_id] = channel.name
                for alias in {channel.name, channel.identifier, numeric_channel_id}:
                    upsert_channel_alias(
                        session,
                        channel_id=alias,
                        name=channel.name,
                        username=username,
                        is_private=channel.private,
                        enabled=channel.enabled,
                    )
            session.commit()
        return name_by_chat_id

    def _store_message(self, session: Session, message, channel_id: str) -> None:
        reactions = getattr(message, "reactions", None)
        upsert_message(
            session,
            channel_id=channel_id,
            message_id=str(message.id),
            message_time=message.date.replace(tzinfo=None),
            raw_text=message.message or "",
            normalized_text=(message.message or "").strip(),
            reply_to_message_id=str(message.reply_to_msg_id) if message.reply_to_msg_id else None,
            forward_from=str(getattr(message, "fwd_from", None)) if getattr(message, "fwd_from", None) else None,
            edit_time=message.edit_date.replace(tzinfo=None) if message.edit_date else None,
            views=getattr(message, "views", None),
            reactions_json=json.dumps(reactions.to_dict()) if reactions else None,
        )

    def _session_path(self) -> str:
        session_dir = Path(self.settings.telegram_session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        return str(session_dir / self.settings.telegram_session_name)


def run_collector() -> None:
    asyncio.run(TelegramCollector().run())
