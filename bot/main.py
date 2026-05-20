import asyncio
import hashlib
import os

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.db.pool import get_pool, close_pool
from bot.handlers import moderation
from bot.handlers import admin_menu
from bot.services.ingestion import run_all_ingestion
from bot.services.processor import process_unprocessed_events

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_HOST = os.environ["WEBHOOK_HOST"]
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = f"/webhook/{hashlib.md5(BOT_TOKEN.encode()).hexdigest()}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"


async def on_startup(bot: Bot) -> None:
    try:
        await get_pool()
        print("[startup] Database connected")
    except Exception as e:
        print(f"[startup] DB warning: {e} — retrying on first request")

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    print(f"[startup] Webhook set: {WEBHOOK_URL}")


async def on_shutdown(bot: Bot) -> None:
    await bot.delete_webhook()
    await close_pool()


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    async def _ingest():
        await run_all_ingestion()

    # Tier 1 (frequent): Telegram channels, YouTube, WB-direct — every 6 hours
    async def _process_frequent():
        await process_unprocessed_events(bot, processing_tier="frequent")

    # Tier 2 (daily): media RSS (vc.ru, oborot.ru, google news) — once per day at 10:00
    async def _process_daily():
        await process_unprocessed_events(bot, processing_tier="daily")

    # Tier 3 (weekly): regulatory/government RSS (kremlin, fas, gov.am) — Mondays at 11:00
    async def _process_weekly():
        await process_unprocessed_events(bot, processing_tier="weekly")

    async def _digest():
        await _send_daily_digest(bot)

    scheduler.add_job(_ingest, "interval", hours=2, id="ingest")
    scheduler.add_job(_process_frequent, "interval", hours=6, id="process_frequent")
    scheduler.add_job(_process_daily, "cron", hour=10, minute=0, id="process_daily")
    scheduler.add_job(_process_weekly, "cron", day_of_week="mon", hour=11, minute=0, id="process_weekly")
    scheduler.add_job(_digest, "cron", hour=9, minute=0, id="digest")
    return scheduler


async def _send_daily_digest(bot: Bot) -> None:
    from bot.db import queries
    from bot.services.processor import _load_keywords, _keyword_passes
    moderator_ids = await queries.get_moderator_ids()

    # Pre-filter: count only keyword-relevant unprocessed events
    all_pending = await queries.get_unprocessed_events(limit=500)
    core_kw, context_kw = await _load_keywords()
    relevant = [
        e for e in all_pending
        if _keyword_passes(
            f"{e.get('title', '')}\n{e.get('body', '')}",
            core_kw, context_kw
        )
    ]
    total = len(relevant)

    if total == 0:
        return

    text = (
        f"📊 <b>Ежедневный дайджест</b>\n\n"
        f"Релевантных событий за ночь: {total}\n"
        f"Запускаю обработку (до 5 за раз, остальное — завтра)..."
    )
    for mod_id in moderator_ids:
        try:
            await bot.send_message(mod_id, text, parse_mode="HTML")
        except Exception:
            pass

    # Process a small batch; scheduler will catch the rest tomorrow
    await process_unprocessed_events(bot)


async def handle_tg_channel_post(bot: Bot, post) -> None:
    """Store incoming channel messages (bot must be admin in the channel)."""
    from bot.db import queries
    text = post.text or post.caption or ""
    if len(text) < 20:
        return
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    sources = await queries.get_active_sources("telegram")
    source_map = {s["identifier"].lstrip("@"): s for s in sources}
    chat_username = (post.chat.username or "").lstrip("@")
    source = source_map.get(chat_username)
    if not source:
        return
    await queries.insert_raw_event(
        source_id=source["id"],
        source_type="telegram",
        external_id=str(post.message_id),
        url=f"https://t.me/{chat_username}/{post.message_id}",
        title=None,
        body=text,
        lang_detected=source["locale"],
        content_hash=content_hash,
    )


def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_menu.router)
    dp.include_router(moderation.router)

    # Store channel posts
    from aiogram import F as AiogramF
    from aiogram.types import Message

    @dp.channel_post()
    async def on_channel_post(message: Message) -> None:
        await handle_tg_channel_post(bot, message)

    scheduler = build_scheduler(bot)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    async def _startup(_app):
        await on_startup(bot)
        scheduler.start()
        asyncio.create_task(_start_telethon())

    async def _shutdown(_app):
        scheduler.shutdown(wait=False)
        from bot.services.telethon_monitor import stop_monitoring
        await stop_monitoring()
        await on_shutdown(bot)

    app.on_startup.append(_startup)
    app.on_cleanup.append(_shutdown)

    web.run_app(app, host="0.0.0.0", port=PORT)


async def _start_telethon():
    try:
        from bot.services.telethon_monitor import start_monitoring
        await start_monitoring()
    except Exception as e:
        print(f"[telethon] Error: {e}")


if __name__ == "__main__":
    main()
