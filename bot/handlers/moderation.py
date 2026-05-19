import hashlib
import io
import os

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from aiogram.filters import Command

from bot.db import queries
from bot.services.publisher import publish_draft


async def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    admin_ids = await queries.get_admin_ids()
    is_admin = user_id in admin_ids
    if is_admin:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📊 Статус"), KeyboardButton(text="▶️ Парсинг")],
                [KeyboardButton(text="⚙️ Управление"), KeyboardButton(text="🤖 Обработать")],
                [KeyboardButton(text="🔍 Поиск"), KeyboardButton(text="📄 Анализ оферты")],
            ],
            resize_keyboard=True,
            persistent=True,
        )
    else:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📊 Статус"), KeyboardButton(text="📄 Анализ оферты")],
                [KeyboardButton(text="🔍 Поиск")],
            ],
            resize_keyboard=True,
            persistent=True,
        )

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

    # Save to Obsidian via GitHub
    try:
        from bot.services.obsidian import save_to_obsidian
        await save_to_obsidian(
            title=f"Черновик #{draft_id}",
            body_ru=draft.get("body_ru", ""),
            label="WB Analytics",
            draft_id=draft_id,
        )
    except Exception:
        pass


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
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id in moderator_ids:
        kb = await get_main_keyboard(message.from_user.id)
        await message.answer(
            f"👋 GoToTop Analytics\n\nВаш ID: <code>{message.from_user.id}</code>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        await message.answer(
            f"Бот аналитики WB.\n\nВаш ID: <code>{message.from_user.id}</code>\n"
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


@router.message(Command("ingest"))
async def cmd_ingest(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        await message.answer("Нет доступа.")
        return
    status_msg = await message.answer("⏳ Парсинг запущен в фоне. Займёт 1-2 минуты...")

    async def _run():
        try:
            from bot.services.ingestion import run_all_ingestion
            results = await run_all_ingestion()
            await message.answer(
                f"✅ Парсинг завершён:\n"
                f"• RSS: {results['rss']} новых\n"
                f"• YouTube: {results['youtube']} новых\n"
                f"• Google News: {results['google_news']} новых\n"
                f"• Форумы (VC.ru): {results.get('forums', 0)} новых\n"
                f"• WB Release Notes: {results.get('wb_release', 0)} новых"
            )
        except Exception as e:
            await message.answer(f"Ошибка парсинга: {e}")

    import asyncio
    asyncio.create_task(_run())


@router.message(Command("process"))
async def cmd_process(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        await message.answer("Нет доступа.")
        return
    await message.answer("⏳ Обработка запущена в фоне...")

    async def _run():
        try:
            from bot.services.processor import process_unprocessed_events
            count = await process_unprocessed_events(message.bot)
            await message.answer(f"✅ Обработано событий через Claude: {count}")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")

    import asyncio
    asyncio.create_task(_run())


@router.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        await message.answer("Нет доступа.")
        return
    rows = await queries.get_sources_by_type()
    lines = ["<b>🔗 Активные источники:</b>\n"]
    type_map = {"telegram": "Telegram", "rss": "RSS", "youtube": "YouTube", "web": "Web"}
    for row in rows:
        t = type_map.get(row["source_type"], row["source_type"])
        lines.append(f"• {t} [{row['locale']}]: {row['cnt']} шт.")
    cost = await queries.get_llm_cost_total()
    lines.append(f"\n💰 Потрачено на Claude: ${cost:.4f}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
    await message.answer(
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Username: @{message.from_user.username}",
        parse_mode="HTML",
    )


# ── Keyboard button handlers ───────────────────────────────────────────────

@router.message(F.text == "📊 Статус")
async def kb_status(message: Message) -> None:
    await cmd_status(message)


@router.message(F.text == "▶️ Парсинг")
async def kb_ingest(message: Message) -> None:
    await cmd_ingest(message)


@router.message(F.text == "🤖 Обработать")
async def kb_process(message: Message) -> None:
    await cmd_process(message)


@router.message(F.text == "⚙️ Управление")
async def kb_menu(message: Message) -> None:
    from bot.handlers.admin_menu import cmd_menu
    await cmd_menu(message)


@router.message(F.text == "📄 Анализ оферты")
async def kb_offer(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    await message.answer(
        "Отправь PDF-файл или документ WB.\n"
        "Я извлеку текст и сделаю анализ через Claude.",
    )


@router.message(F.text == "🔍 Поиск")
async def kb_search_prompt(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    await message.answer("Введи поисковый запрос:\n/search <запрос>\n\nПример: /search ФАС комиссия")


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    query = message.text.replace("/search", "").strip()
    if not query:
        await message.answer("Укажи запрос: /search ФАС комиссия")
        return
    results = await queries.search_events(query, limit=5)
    if not results:
        await message.answer(f"По запросу «{query}» ничего не найдено.")
        return
    lines = [f"🔍 <b>Результаты по «{query}»:</b>\n"]
    for r in results:
        url = r.get("url", "")
        title = r.get("title") or r.get("body", "")[:80]
        date = str(r.get("fetched_at", ""))[:10]
        link = f'<a href="{url}">{title}</a>' if url else title
        lines.append(f"• {date} — {link}")
    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


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


@router.callback_query(F.data.startswith("offer_post:"))
async def handle_offer_post(callback: CallbackQuery) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if callback.from_user.id not in moderator_ids:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Генерирую пост...")
    await callback.message.answer("⏳ Генерирую пост через Claude Sonnet...")

    try:
        # Get the last offer raw_event
        from bot.db.pool import get_pool
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM raw_events WHERE source_type='offer' ORDER BY fetched_at DESC LIMIT 1"
        )
        if not row:
            await callback.message.answer("Не найдена оферта в базе.")
            return

        from bot.services.llm import classify_and_summarize, generate_post
        text = f"{row['title'] or ''}\n{row['body'] or ''}"
        classification = await classify_and_summarize(text)

        post = await generate_post(
            label=classification.get("label", "Изменение_оферты"),
            summary_ru=classification.get("summary_ru", ""),
            entities=classification.get("entities", []),
            confidence_band="confirmed_official",
        )

        draft_id = await queries.create_draft(
            body_ru=post.get("body_ru", ""),
            body_hy=post.get("body_hy", ""),
        )

        from bot.services.publisher import send_draft_to_moderators
        await send_draft_to_moderators(
            draft_id=draft_id,
            body_ru=post.get("body_ru", ""),
            body_hy=post.get("body_hy", ""),
            bot=callback.bot,
            tier_label="🔴 ОФЕРТА",
        )
        await callback.message.answer(f"✅ Черновик #{draft_id} создан и отправлен на модерацию.")

    except Exception as e:
        await callback.message.answer(f"Ошибка: {e}")
