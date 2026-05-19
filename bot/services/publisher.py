import asyncio
from aiogram import Bot

from bot.db import queries


async def publish_draft(draft: dict, locale: str, bot: Bot) -> int:
    body = draft["body_ru"] if locale == "ru" else draft["body_hy"]
    if not body:
        return 0

    channels = await queries.get_publish_channels(locale)
    published = 0
    for channel_id in channels:
        try:
            msg = await bot.send_message(channel_id, body, parse_mode="HTML")
            await queries.log_publish(draft["id"], channel_id, locale, msg.message_id)
            published += 1
            await asyncio.sleep(0.5)  # stay within Telegram rate limits
        except Exception as e:
            print(f"[publisher] Failed to send to {channel_id}: {e}")

    return published


async def send_draft_to_moderators(
    draft_id: int, body_ru: str, body_hy: str, bot: Bot, tier_label: str = "🟡 Дайджест"
) -> None:
    from bot.handlers.moderation import draft_keyboard

    moderator_ids = await queries.get_moderator_ids()
    preview = (
        f"📋 <b>Черновик #{draft_id}</b> {tier_label}\n\n"
        f"🇷🇺 <b>RU:</b>\n{body_ru[:800]}\n\n"
        f"🇦🇲 <b>HY:</b>\n{body_hy[:400] if body_hy else '—'}"
    )
    for mod_id in moderator_ids:
        try:
            await bot.send_message(
                mod_id,
                preview,
                parse_mode="HTML",
                reply_markup=draft_keyboard(draft_id),
            )
        except Exception as e:
            print(f"[publisher] Cannot reach moderator {mod_id}: {e}")
