import hashlib
import os
import asyncio
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from bot.db import queries

API_ID = int(os.environ.get("TG_API_ID", "0"))
API_HASH = os.environ.get("TG_API_HASH", "")
TG_SESSION = os.environ.get("TG_SESSION", "")

_client: TelegramClient | None = None


async def get_client() -> TelegramClient:
    global _client
    if _client is None or not _client.is_connected():
        _client = TelegramClient(
            StringSession(TG_SESSION), API_ID, API_HASH,
            system_version="4.16.30-vxCUSTOM",
        )
        await _client.start()
    return _client


async def start_monitoring() -> None:
    if not TG_SESSION or not API_ID or not API_HASH:
        print("[telethon] Skipping — TG_SESSION/API_ID/API_HASH not set")
        return

    client = await get_client()
    sources = await queries.get_active_sources("telegram")
    channel_map = {}

    for source in sources:
        username = source["identifier"].lstrip("@")
        try:
            entity = await client.get_entity(username)
            channel_map[entity.id] = source
            print(f"[telethon] Monitoring: @{username}")
        except Exception as e:
            print(f"[telethon] Cannot resolve @{username}: {e}")

    @client.on(events.NewMessage(chats=list(channel_map.keys())))
    async def on_message(event):
        source = channel_map.get(event.chat_id)
        if not source:
            return
        text = event.message.text or event.message.caption or ""
        if len(text) < 30:
            return
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        username = source["identifier"].lstrip("@")
        await queries.insert_raw_event(
            source_id=source["id"],
            source_type="telegram",
            external_id=str(event.message.id),
            url=f"https://t.me/{username}/{event.message.id}",
            title=None,
            body=text,
            lang_detected=source["locale"],
            content_hash=content_hash,
        )
        print(f"[telethon] Saved from @{username}: {text[:60]}...")

    print(f"[telethon] Listening to {len(channel_map)} channels")
    await client.run_until_disconnected()


async def stop_monitoring() -> None:
    global _client
    if _client and _client.is_connected():
        await _client.disconnect()
        _client = None
