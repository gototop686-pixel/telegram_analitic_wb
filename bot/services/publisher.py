import asyncio
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.db import queries


def channel_post_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Keyboard attached to auto-published posts in channels."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить из базы", callback_data=f"ch_del:{draft_id}"),
    ]])


def unclear_draft_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """Keyboard for unclear-market drafts in drafts channel."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 В RU канал", callback_data=f"approve:{draft_id}:ru"),
            InlineKeyboardButton(text="🇦🇲 В HY канал", callback_data=f"approve:{draft_id}:hy"),
        ],
        [
            InlineKeyboardButton(text="🇷🇺🇦🇲 В оба", callback_data=f"approve:{draft_id}:both"),
            InlineKeyboardButton(text="❌ Удалить", callback_data=f"reject:{draft_id}"),
        ],
    ])


async def auto_publish_by_market(
    draft: dict,
    market: str,
    bot: Bot,
    label: str = "",
    source_url: str = "",
    summary_ru: str = "",
) -> int:
    """Auto-publish draft to channels based on market classification."""
    draft_id = draft["id"]
    body_ru = draft.get("body_ru", "")
    body_hy = draft.get("body_hy", "")
    published = 0

    # Save to Obsidian
    obsidian_path = ""
    try:
        from bot.services.obsidian import save_published_to_obsidian
        obsidian_path = await save_published_to_obsidian(
            draft_id=draft_id, body_ru=body_ru, body_hy=body_hy,
            label=label, market=market, source_url=source_url, summary_ru=summary_ru,
        )
    except Exception as e:
        print(f"[obsidian] Published save failed: {e}")

    kb = channel_post_keyboard(draft_id)

    if market in ("RU", "both"):
        channels = await queries.get_publish_channels("ru")
        for ch_id in channels:
            try:
                msg = await bot.send_message(ch_id, body_ru, parse_mode="HTML", reply_markup=kb)
                await queries.log_publish_extended(draft_id, ch_id, "ru", msg.message_id, market, obsidian_path)
                published += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[publisher] RU channel {ch_id} error: {e}")

    if market in ("HY", "both"):
        channels = await queries.get_publish_channels("hy")
        body = body_hy or body_ru
        for ch_id in channels:
            try:
                msg = await bot.send_message(ch_id, body, parse_mode="HTML", reply_markup=kb)
                await queries.log_publish_extended(draft_id, ch_id, "hy", msg.message_id, market, obsidian_path)
                published += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[publisher] HY channel {ch_id} error: {e}")

    if market == "unclear":
        drafts_chat_id = await queries.get_setting("drafts_chat_id", "")
        if drafts_chat_id:
            try:
                preview = (
                    f"❓ <b>Рынок не определён</b> · {label}\n\n"
                    f"🇷🇺 {body_ru[:500]}\n\n"
                    f"🇦🇲 {body_hy[:300] if body_hy else '—'}"
                )
                await bot.send_message(
                    int(drafts_chat_id), preview,
                    parse_mode="HTML",
                    reply_markup=unclear_draft_keyboard(draft_id),
                )
            except Exception as e:
                print(f"[publisher] Drafts chat error: {e}")

    return published


async def publish_draft(draft: dict, locale: str, bot: Bot) -> int:
    """Manual publish (called from approve callback)."""
    body = draft["body_ru"] if locale == "ru" else draft["body_hy"]
    if not body:
        return 0
    channels = await queries.get_publish_channels(locale)
    published = 0
    kb = channel_post_keyboard(draft["id"])
    for channel_id in channels:
        try:
            msg = await bot.send_message(channel_id, body, parse_mode="HTML", reply_markup=kb)
            await queries.log_publish_extended(
                draft["id"], channel_id, locale, msg.message_id,
                market="RU" if locale == "ru" else "HY",
            )
            published += 1
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"[publisher] Failed to send to {channel_id}: {e}")
    return published


async def send_draft_to_moderators(
    draft_id: int, body_ru: str, body_hy: str, bot: Bot,
    tier_label: str = "🟡 Дайджест", source_info: str = "",
    label: str = "", confidence: float = 0,
) -> None:
    """Send draft to moderator DMs (used for critical tier-1 alerts)."""
    from bot.handlers.moderation import draft_keyboard
    conf_str = f"{confidence:.0%}" if confidence else ""
    preview = (
        f"📋 <b>Черновик #{draft_id}</b> {tier_label}\n"
        f"🏷 {label} {conf_str}\n"
        f"🔗 {source_info}\n\n"
        f"🇷🇺 <b>RU:</b>\n{body_ru[:600]}\n\n"
        f"🇦🇲 <b>HY:</b>\n{body_hy[:300] if body_hy else '—'}"
    )
    moderator_ids = await queries.get_moderator_ids()
    for mod_id in moderator_ids:
        try:
            await bot.send_message(mod_id, preview, parse_mode="HTML", reply_markup=draft_keyboard(draft_id))
        except Exception as e:
            print(f"[publisher] Cannot reach moderator {mod_id}: {e}")
