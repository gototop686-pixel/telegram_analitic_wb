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
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[publisher] Failed to send to {channel_id}: {e}")

    return published


async def send_draft_to_moderators(
    draft_id: int, body_ru: str, body_hy: str, bot: Bot,
    tier_label: str = "🟡 Дайджест", source_info: str = "",
    label: str = "", confidence: float = 0,
) -> None:
    from bot.handlers.moderation import draft_keyboard

    preview = _build_draft_preview(draft_id, tier_label, label, confidence, source_info, body_ru, body_hy)
    kb = draft_keyboard(draft_id)

    # Send to individual moderators (DM)
    moderator_ids = await queries.get_moderator_ids()
    for mod_id in moderator_ids:
        try:
            await bot.send_message(mod_id, preview, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            print(f"[publisher] Cannot reach moderator {mod_id}: {e}")

    # Also send to drafts review chat if configured
    drafts_chat_id = await queries.get_setting("drafts_chat_id", "")
    if drafts_chat_id:
        try:
            await bot.send_message(int(drafts_chat_id), preview, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            print(f"[publisher] Cannot send to drafts chat {drafts_chat_id}: {e}")


def _build_draft_preview(
    draft_id: int, tier_label: str, label: str,
    confidence: float, source_info: str, body_ru: str, body_hy: str,
) -> str:
    conf_str = f"{confidence:.0%}" if confidence else ""
    return (
        f"📋 <b>Черновик #{draft_id}</b> {tier_label}\n"
        f"🏷 {label} {conf_str}\n"
        f"🔗 {source_info}\n\n"
        f"🇷🇺 <b>RU:</b>\n{body_ru[:800]}\n\n"
        f"🇦🇲 <b>HY:</b>\n{body_hy[:400] if body_hy else '—'}"
    )
