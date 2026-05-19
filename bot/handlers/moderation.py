import hashlib
import io
import os

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from bot.db import queries
from bot.services.publisher import publish_draft

router = Router()


def draft_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать RU", callback_data=f"approve:{draft_id}:ru"),
            InlineKeyboardButton(text="✅ Опубликовать HY", callback_data=f"approve:{draft_id}:hy"),
        ],
        [
            InlineKeyboardButton(text="✅ Оба языка", callback_data=f"approve:{draft_id}:both"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{draft_id}"),
        ],
    ])


@router.callback_query(F.data.startswith("approve:"))
async def handle_approve(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    draft_id = int(parts[1])
    locale = parts[2]

    moderator_ids = await queries.get_moderator_ids()
    if callback.from_user.id not in moderator_ids:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    draft = await queries.get_draft(draft_id)
    if not draft or draft["status"] != "pending":
        await callback.answer("Черновик уже обработан.", show_alert=True)
        return

    await queries.approve_draft(draft_id, callback.from_user.id)

    locales = ["ru", "hy"] if locale == "both" else [locale]
    published_count = 0
    for loc in locales:
        published_count += await publish_draft(draft, loc, callback.bot)

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ Опубликовано в {published_count} каналах. @{callback.from_user.username}",
        reply_markup=None,
    )
    await callback.answer("Опубликовано!")


@router.callback_query(F.data.startswith("reject:"))
async def handle_reject(callback: CallbackQuery) -> None:
    draft_id = int(callback.data.split(":")[1])

    moderator_ids = await queries.get_moderator_ids()
    if callback.from_user.id not in moderator_ids:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await queries.reject_draft(draft_id)
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ Отклонено.",
        reply_markup=None,
    )
    await callback.answer("Отклонено.")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        f"Бот аналитики WB запущен.\n\nВаш ID: <code>{message.from_user.id}</code>\n"
        "Передайте этот ID администратору для получения доступа.",
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        await message.answer("Нет доступа.")
        return

    stats = await queries.get_bot_stats()
    text = (
        "<b>📊 Статус бота</b>\n\n"
        f"📥 Всего событий в БД: <b>{stats['raw_total']}</b>\n"
        f"⏳ Не обработано: <b>{stats['unprocessed']}</b>\n"
        f"📝 Черновиков на проверке: <b>{stats['drafts_pending']}</b>\n"
        f"📢 Опубликовано постов: <b>{stats['published']}</b>\n"
        f"🔗 Активных источников: <b>{stats['sources']}</b>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("addmod"))
async def cmd_addmod(message: Message) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await message.answer("Только для администраторов.")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /addmod <user_id> [moderator|admin]\nПример: /addmod 123456789 moderator")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("Неверный ID пользователя.")
        return

    role = parts[2] if len(parts) > 2 and parts[2] in ("admin", "moderator", "viewer") else "moderator"
    await queries.upsert_user(target_id, None, role)
    await message.answer(f"✅ Пользователь <code>{target_id}</code> добавлен как <b>{role}</b>.", parse_mode="HTML")


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
    await message.answer(
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Username: @{message.from_user.username}",
        parse_mode="HTML",
    )


@router.message(F.document | F.text.startswith("/offer"))
async def handle_offer_upload(message: Message) -> None:
    """Handle WB offer/document upload for analysis."""
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return

    text_to_analyze = None

    if message.document:
        mime = message.document.mime_type or ""
        if "pdf" not in mime and "text" not in mime:
            await message.answer("Поддерживаются только PDF и текстовые файлы.")
            return

        await message.answer("⏳ Скачиваю и анализирую документ...")
        import asyncio
        file = await message.bot.get_file(message.document.file_id)
        try:
            file_bytes = await asyncio.wait_for(
                message.bot.download_file(file.file_path), timeout=30
            )
        except asyncio.TimeoutError:
            await message.answer("Timeout при скачивании файла. Попробуй ещё раз.")
            return
        raw = file_bytes.read() if hasattr(file_bytes, "read") else bytes(file_bytes)

        if "pdf" in mime:
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(raw)) as pdf:
                    pages = []
                    for page in pdf.pages[:25]:  # max 25 pages
                        t = page.extract_text()
                        if t:
                            pages.append(t)
                text_to_analyze = "\n".join(pages)[:10000]  # max 10k chars to Claude
            except Exception as e:
                await message.answer(f"Ошибка чтения PDF: {e}")
                return
        else:
            text_to_analyze = raw.decode("utf-8", errors="ignore")[:10000]

    elif message.caption:
        text_to_analyze = message.caption

    if not text_to_analyze or len(text_to_analyze.strip()) < 50:
        await message.answer(
            "Отправь PDF-файл или документ оферты WB.\n"
            "Я извлеку текст и сделаю анализ с помощью Claude."
        )
        return

    await message.answer("🤖 Анализирую через Claude Sonnet...")

    try:
        from bot.services.llm import analyze_offer
        result = await analyze_offer(text_to_analyze)

        changes = "\n".join(f"• {c}" for c in result.get("key_changes", []))
        risks = "\n".join(f"• {r}" for r in result.get("risks", []))
        numbers = "\n".join(f"• {n}" for n in result.get("numbers", []))

        urgency_emoji = "🔴" if result.get("urgency") == 1 else "🟡"

        analysis_text = (
            f"{urgency_emoji} <b>{result.get('title', 'Анализ документа WB')}</b>\n\n"
            f"<b>Ключевые изменения:</b>\n{changes}\n\n"
            f"<b>Риски для продавца:</b>\n{risks}\n\n"
            f"<b>Важные цифры:</b>\n{numbers}\n\n"
            f"<b>Что делать:</b> {result.get('action_required', '—')}\n\n"
            f"<b>Резюме:</b> {result.get('summary_ru', '—')}"
        )

        await message.answer(analysis_text, parse_mode="HTML")

        content_hash = hashlib.sha256(text_to_analyze.encode()).hexdigest()
        await queries.insert_raw_event(
            source_id=None,
            source_type="offer",
            external_id=None,
            url=None,
            title=result.get("title", "Оферта WB"),
            body=text_to_analyze[:5000],
            lang_detected="ru",
            content_hash=content_hash,
        )

        if result.get("urgency") == 1:
            await message.answer(
                "⚠️ Документ помечен как <b>КРИТИЧНЫЙ</b>. Создать черновик поста для публикации?",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Создать пост", callback_data=f"offer_post:{content_hash[:16]}"),
                    InlineKeyboardButton(text="❌ Не надо", callback_data="offer_skip"),
                ]]),
            )

    except Exception as e:
        await message.answer(f"Ошибка анализа: {e}")


@router.callback_query(F.data == "offer_skip")
async def handle_offer_skip(callback: CallbackQuery) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Пропущено.")
