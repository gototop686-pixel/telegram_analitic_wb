import asyncio
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
                [KeyboardButton(text="📝 Черновики"), KeyboardButton(text="📄 Анализ оферты")],
                [KeyboardButton(text="🔗 Анализ ссылки"), KeyboardButton(text="🧠 Стратегии")],
                [KeyboardButton(text="🔍 Поиск")],
            ],
            resize_keyboard=True,
            persistent=True,
        )
    else:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📊 Статус"), KeyboardButton(text="📝 Черновики")],
                [KeyboardButton(text="📄 Анализ оферты"), KeyboardButton(text="🔍 Поиск")],
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
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    await message.answer(
        "▶️ <b>Выбери что парсить:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📱 Telegram каналы", callback_data="ingest:telegram")],
            [InlineKeyboardButton(text="📡 RSS источники", callback_data="ingest:rss")],
            [InlineKeyboardButton(text="🔍 Google News", callback_data="ingest:google")],
            [InlineKeyboardButton(text="📰 Форумы (VC.ru, Ozon)", callback_data="ingest:forums")],
            [InlineKeyboardButton(text="🌐 Всё сразу", callback_data="ingest:all")],
        ]),
    )


@router.callback_query(F.data.startswith("ingest:"))
async def handle_ingest_choice(callback: CallbackQuery) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if callback.from_user.id not in moderator_ids:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    choice = callback.data.split(":")[1]
    labels = {
        "telegram": "Telegram каналы", "rss": "RSS источники",
        "google": "Google News", "forums": "Форумы", "all": "все источники",
    }
    await callback.message.edit_text(f"⏳ Парсинг: {labels.get(choice)}...")
    await callback.answer()

    async def _run():
        try:
            from bot.services.ingestion import (
                run_all_ingestion, run_rss_ingestion,
                run_google_news_ingestion, run_forum_ingestion,
            )
            if choice == "all":
                r = await run_all_ingestion()
                text = (
                    f"✅ Парсинг завершён:\n"
                    f"• RSS: {r['rss']} новых\n"
                    f"• Google News: {r['google_news']} новых\n"
                    f"• Форумы: {r.get('forums', 0)} новых\n"
                    f"• WB Release: {r.get('wb_release', 0)} новых"
                )
            elif choice == "rss":
                n = await run_rss_ingestion()
                text = f"✅ RSS: {n} новых событий"
            elif choice == "google":
                n = await run_google_news_ingestion()
                text = f"✅ Google News: {n} новых событий"
            elif choice == "forums":
                n = await run_forum_ingestion()
                text = f"✅ Форумы: {n} новых событий"
            else:
                text = "✅ Telegram каналы парсятся через Telethon автоматически."
            await callback.message.answer(text)
        except Exception as e:
            await callback.message.answer(f"Ошибка парсинга: {e}")

    asyncio.create_task(_run())


@router.message(F.text == "🤖 Обработать")
async def kb_process(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    # Show queue summary
    by_type = await queries.get_unprocessed_by_type()
    total = sum(r["cnt"] for r in by_type)
    if total == 0:
        await message.answer("✅ Очередь пуста — все события обработаны.")
        return
    lines = [f"🤖 <b>В очереди: {total} событий</b>\n"]
    type_icons = {"telegram": "📱", "rss": "📡", "youtube": "▶️", "offer": "📄"}
    buttons = []
    for row in by_type:
        t = row["source_type"]
        icon = type_icons.get(t, "📌")
        lines.append(f"{icon} {t.upper()}: <b>{row['cnt']}</b>")
        buttons.append([InlineKeyboardButton(
            text=f"{icon} Обработать {t.upper()} ({row['cnt']})",
            callback_data=f"process:type:{t}",
        )])
    lines.append("\n<i>Совет: сначала очисти фильтром — это бесплатно и быстро.</i>")
    buttons.insert(0, [InlineKeyboardButton(
        text=f"🧹 Очистить нерелевантное (бесплатно)",
        callback_data="process:filter",
    )])
    buttons.append([InlineKeyboardButton(
        text=f"🌐 Обработать всё (до 30 событий)",
        callback_data="process:type:all",
    )])
    await message.answer(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("process:"))
async def handle_process_choice(callback: CallbackQuery) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if callback.from_user.id not in moderator_ids:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    action = callback.data.split(":")[2] if len(callback.data.split(":")) > 2 else ""

    if callback.data == "process:filter":
        await callback.message.edit_text("🧹 Запускаю бесплатный фильтр по ключевым словам...")
        await callback.answer()
        async def _filter():
            try:
                from bot.services.processor import batch_keyword_filter
                result = await batch_keyword_filter()
                await callback.message.answer(
                    f"🧹 <b>Фильтрация завершена:</b>\n\n"
                    f"❌ Удалено нерелевантных: <b>{result['skipped']}</b>\n"
                    f"✅ Осталось для Claude: <b>{result['kept']}</b>\n\n"
                    f"Теперь нажми <b>🤖 Обработать</b> снова.",
                    parse_mode="HTML",
                )
            except Exception as e:
                await callback.message.answer(f"Ошибка: {e}")
        asyncio.create_task(_filter())
        return

    source_type = action
    label = "всё" if source_type == "all" else source_type.upper()
    await callback.message.edit_text(f"⏳ Обрабатываю через Claude: {label}...")
    await callback.answer()

    async def _process():
        try:
            from bot.services.processor import process_unprocessed_events, process_by_source_type
            if source_type == "all":
                count = await process_unprocessed_events(callback.bot)
            else:
                count = await process_by_source_type(callback.bot, source_type)
            await callback.message.answer(
                f"✅ Обработано через Claude: <b>{count}</b> событий\n"
                f"Черновики отправлены на проверку — нажми <b>📝 Черновики</b>.",
                parse_mode="HTML",
            )
        except Exception as e:
            await callback.message.answer(f"Ошибка: {e}")

    asyncio.create_task(_process())


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


@router.message(F.text == "📝 Черновики")
async def kb_drafts(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    await show_pending_drafts(message)


async def show_pending_drafts(message: Message) -> None:
    drafts = await queries.get_pending_drafts(limit=10)
    if not drafts:
        await message.answer("✅ Нет черновиков на проверке.")
        return
    await message.answer(f"📝 <b>Черновики на проверке: {len(drafts)}</b>", parse_mode="HTML")
    for draft in drafts:
        body_ru = draft.get("body_ru", "")
        body_hy = draft.get("body_hy", "")
        created = str(draft.get("created_at", ""))[:16]
        preview = (
            f"📋 <b>Черновик #{draft['id']}</b> · {created}\n\n"
            f"🇷🇺 <b>RU:</b>\n{body_ru[:600]}\n\n"
            f"🇦🇲 <b>HY:</b>\n{body_hy[:300] if body_hy else '—'}"
        )
        await message.answer(preview, parse_mode="HTML", reply_markup=draft_keyboard(draft["id"]))


@router.message(F.text == "🔗 Анализ ссылки")
async def kb_analyze_link(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    await message.answer(
        "Отправь ссылку (http://...) — я загружу страницу и проанализирую через Claude.\n\n"
        "Или: /analyze @channelname — чтобы добавить TG-канал в мониторинг."
    )


@router.message(F.text == "🧠 Стратегии")
async def kb_strategies(message: Message) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await message.answer("Только для администраторов.")
        return
    from bot.handlers.admin_menu import cmd_menu
    await cmd_menu(message)


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


async def _extract_text_from_doc(message: Message) -> str | None:
    mime = message.document.mime_type or ""
    if "pdf" not in mime and "text" not in mime:
        await message.answer("Поддерживаются только PDF и текстовые файлы.")
        return None
    file = await message.bot.get_file(message.document.file_id)
    try:
        file_bytes = await asyncio.wait_for(message.bot.download_file(file.file_path), timeout=30)
    except asyncio.TimeoutError:
        await message.answer("Timeout при скачивании. Попробуй ещё раз.")
        return None
    raw = file_bytes.read() if hasattr(file_bytes, "read") else bytes(file_bytes)
    if "pdf" in mime:
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages[:25]]
            return "\n".join(pages)[:12000]
        except Exception as e:
            await message.answer(f"Ошибка чтения PDF: {e}")
            return None
    return raw.decode("utf-8", errors="ignore")[:12000]


from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext


class OfferFSM(StatesGroup):
    waiting_locale = State()


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    await message.answer("⏳ Скачиваю документ...")
    text = await _extract_text_from_doc(message)
    if not text or len(text.strip()) < 50:
        await message.answer("Не удалось извлечь текст из документа.")
        return
    filename = message.document.file_name or "document"
    await state.update_data(offer_text=text, offer_filename=filename)
    await state.set_state(OfferFSM.waiting_locale)
    await message.answer(
        f"📄 Документ получен: <b>{filename}</b>\n\nВыбери тип документа:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Оферта RU", callback_data="offer_locale:ru"),
                InlineKeyboardButton(text="🇦🇲 Оферта HY", callback_data="offer_locale:hy"),
            ],
            [
                InlineKeyboardButton(text="🇰🇿 Оферта KZ", callback_data="offer_locale:kz"),
                InlineKeyboardButton(text="❌ Просто анализ", callback_data="offer_locale:analyze_only"),
            ],
        ]),
    )


@router.callback_query(F.data.startswith("offer_locale:"))
async def handle_offer_locale(callback: CallbackQuery, state: FSMContext) -> None:
    locale = callback.data.split(":")[1]
    data = await state.get_data()
    text = data.get("offer_text", "")
    filename = data.get("offer_filename", "")
    await state.clear()
    if locale == "analyze_only":
        await callback.message.edit_text("🤖 Запускаю анализ через Claude Sonnet...")
        await _do_offer_analysis(callback.message, text, filename, callback.from_user.id)
        await callback.answer()
        return
    existing = await queries.get_stored_offer(locale)
    locale_name = {"ru": "Русская", "hy": "Армянская", "kz": "Казахстанская"}.get(locale, locale)
    await state.update_data(offer_text=text, offer_filename=filename, offer_locale=locale)
    if existing:
        old_date = str(existing.get("uploaded_at", ""))[:10]
        old_file = existing.get("filename", "—")
        await callback.message.edit_text(
            f"📂 Уже есть базовая <b>{locale_name}</b> версия:\n• {old_file} ({old_date})\n\nЧто сделать?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Заменить + сравнить", callback_data="offer_action:replace_compare")],
                [InlineKeyboardButton(text="📊 Только сравнить", callback_data="offer_action:compare_only")],
                [InlineKeyboardButton(text="💾 Заменить без сравнения", callback_data="offer_action:replace_only")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="offer_action:cancel")],
            ]),
        )
    else:
        await callback.message.edit_text(
            f"Сохранить как базовую <b>{locale_name}</b> версию оферты?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Сохранить", callback_data="offer_action:save_new")],
                [InlineKeyboardButton(text="📊 Только анализ", callback_data="offer_action:analyze_only")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="offer_action:cancel")],
            ]),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("offer_action:"))
async def handle_offer_action(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":")[1]
    data = await state.get_data()
    text = data.get("offer_text", "")
    filename = data.get("offer_filename", "")
    locale = data.get("offer_locale", "ru")
    await state.clear()
    if action == "cancel":
        await callback.message.edit_text("Отменено.")
        await callback.answer()
        return
    if action in ("save_new", "replace_only"):
        await queries.save_stored_offer(locale, text, filename, callback.from_user.id, filename)
        locale_name = {"ru": "Русская", "hy": "Армянская", "kz": "Казахстанская"}.get(locale, locale)
        await callback.message.edit_text(f"✅ <b>{locale_name}</b> версия сохранена как базовая: {filename}", parse_mode="HTML")
        await callback.answer()
        return
    if action == "analyze_only":
        await callback.message.edit_text("🤖 Анализирую через Claude...")
        await _do_offer_analysis(callback.message, text, filename, callback.from_user.id)
        await callback.answer()
        return
    if action in ("replace_compare", "compare_only"):
        existing = await queries.get_stored_offer(locale)
        old_text = existing["text_content"] if existing else ""
        await callback.message.edit_text("🔄 Сравниваю версии через Claude Sonnet...")
        try:
            from bot.services.llm import compare_offers
            result = await compare_offers(old_text, text)
            urgency_emoji = "🔴" if result.get("urgency") == 1 else "🟡"
            if not result.get("has_changes"):
                await callback.message.answer("✅ Документы идентичны — изменений не обнаружено.")
            else:
                parts = [f"{urgency_emoji} <b>Сравнение версий оферты WB</b>\n"]
                critical = "\n".join(f"  ⚠️ {x}" for x in result.get("critical_changes", []))
                numbers = "\n".join(f"  💰 {x}" for x in result.get("numbers_changed", []))
                changed = "\n".join(f"  🔄 {x}" for x in result.get("changed", []))
                added = "\n".join(f"  ✅ {x}" for x in result.get("added", []))
                removed = "\n".join(f"  ❌ {x}" for x in result.get("removed", []))
                if critical: parts.append(f"<b>🚨 Критично:</b>\n{critical}")
                if numbers: parts.append(f"<b>💰 Цифры:</b>\n{numbers}")
                if changed: parts.append(f"<b>🔄 Изменено:</b>\n{changed}")
                if added: parts.append(f"<b>✅ Добавлено:</b>\n{added}")
                if removed: parts.append(f"<b>❌ Удалено:</b>\n{removed}")
                parts.append(f"<b>Резюме:</b> {result.get('summary_ru', '—')}")
                await callback.message.answer("\n\n".join(parts), parse_mode="HTML")
                if result.get("urgency") == 1:
                    content_hash = hashlib.sha256(text.encode()).hexdigest()
                    await callback.message.answer(
                        "⚠️ Критичные изменения! Создать пост?", parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="✅ Создать пост", callback_data=f"offer_post:{content_hash[:16]}"),
                            InlineKeyboardButton(text="❌ Не надо", callback_data="offer_skip"),
                        ]]),
                    )
        except Exception as e:
            await callback.message.answer(f"Ошибка сравнения: {e}")
        if action == "replace_compare":
            await queries.save_stored_offer(locale, text, filename, callback.from_user.id, filename)
            await callback.message.answer(f"💾 Новая версия сохранена как базовая ({filename}).")
    await callback.answer()


async def _do_offer_analysis(message: Message, text: str, filename: str, user_id: int) -> None:
    try:
        from bot.services.llm import analyze_offer
        result = await analyze_offer(text)
        urgency_emoji = "🔴" if result.get("urgency") == 1 else "🟡"
        changes = "\n".join(f"• {c}" for c in result.get("key_changes", []))
        risks = "\n".join(f"• {r}" for r in result.get("risks", []))
        numbers = "\n".join(f"• {n}" for n in result.get("numbers", []))
        analysis_text = (
            f"{urgency_emoji} <b>{result.get('title', filename)}</b>\n\n"
            f"<b>Ключевые пункты:</b>\n{changes}\n\n"
            f"<b>Риски:</b>\n{risks}\n\n"
            f"<b>Цифры:</b>\n{numbers}\n\n"
            f"<b>Что делать:</b> {result.get('action_required', '—')}\n\n"
            f"<b>Резюме:</b> {result.get('summary_ru', '—')}"
        )
        await message.answer(analysis_text, parse_mode="HTML")
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        await queries.insert_raw_event(source_id=None, source_type="offer", external_id=None, url=None,
            title=result.get("title", filename), body=text[:5000], lang_detected="ru", content_hash=content_hash)
        if result.get("urgency") == 1:
            await message.answer("⚠️ Документ <b>КРИТИЧНЫЙ</b>. Создать пост?", parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Создать пост", callback_data=f"offer_post:{content_hash[:16]}"),
                    InlineKeyboardButton(text="❌ Не надо", callback_data="offer_skip"),
                ]]))
    except Exception as e:
        await message.answer(f"Ошибка анализа: {e}")


@router.message(Command("offer"))
async def handle_offer_text_cmd(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    await message.answer("Отправь PDF-файл оферты WB — я его проанализирую.")


@router.message(Command("analyze"))
async def cmd_analyze(message: Message) -> None:
    """Analyze a URL, Telegram channel, or forum link on demand."""
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    target = message.text.replace("/analyze", "").strip()
    if not target:
        await message.answer(
            "Использование:\n"
            "/analyze https://example.com — анализ ссылки\n"
            "/analyze @channelname — добавить TG-канал в мониторинг\n\n"
            "Или просто отправь ссылку в чат — я её обработаю автоматически."
        )
        return
    if target.startswith("@") or (not target.startswith("http") and not target.startswith("/")):
        identifier = target if target.startswith("@") else f"@{target}"
        ok = await queries.add_source("telegram", "media", "ru", identifier)
        if ok:
            await message.answer(f"✅ Канал {identifier} добавлен в мониторинг.\nДанные появятся при следующем парсинге (▶️ Парсинг).")
        else:
            await message.answer(f"⚠️ Канал {identifier} уже есть в источниках.")
        return
    await _analyze_url(message, target)


@router.message(F.text.startswith("http"))
async def handle_url_message(message: Message) -> None:
    """Auto-detect URL sent by moderator and analyze it."""
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    url = message.text.strip().split()[0]
    await _analyze_url(message, url)


async def _analyze_url(message: Message, url: str) -> None:
    await message.answer(f"⏳ Загружаю и анализирую:\n<code>{url}</code>", parse_mode="HTML")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                await message.answer(f"Ошибка загрузки: HTTP {resp.status_code}")
                return
            html = resp.text

        # Simple text extraction
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()[:8000]

        if len(text) < 100:
            await message.answer("Не удалось извлечь текст со страницы.")
            return

        from bot.services.llm import classify_and_summarize, generate_post
        classification = await classify_and_summarize(text)
        label = classification.get("label", "")
        confidence = classification.get("confidence", 0)
        alert_tier = classification.get("alert_tier", 2)
        tier_emoji = "🔴 КРИТИЧНО" if alert_tier == 1 else "🟡 Дайджест"

        result_text = (
            f"{tier_emoji} <b>{label}</b> ({confidence:.0%})\n\n"
            f"<b>Резюме:</b> {classification.get('summary_ru', '—')}\n\n"
            f"<b>Сущности:</b> {', '.join(classification.get('entities', []))}"
        )
        await message.answer(result_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✍️ Создать черновик поста", callback_data=f"url_post:{hash(url) % 10**8}"),
                InlineKeyboardButton(text="💾 Сохранить в Obsidian", callback_data=f"url_obs:{hash(url) % 10**8}"),
            ]]))

        # Store result for follow-up callbacks
        import bot.handlers.moderation as _self
        if not hasattr(_self, "_url_cache"):
            _self._url_cache = {}
        _self._url_cache[hash(url) % 10**8] = {
            "url": url, "text": text, "classification": classification
        }

    except Exception as e:
        await message.answer(f"Ошибка анализа: {e}")


@router.callback_query(F.data.startswith("url_post:"))
async def handle_url_post(callback: CallbackQuery) -> None:
    import bot.handlers.moderation as _self
    cache = getattr(_self, "_url_cache", {})
    key = int(callback.data.split(":")[1])
    data = cache.get(key)
    if not data:
        await callback.answer("Данные устарели. Отправь ссылку ещё раз.", show_alert=True)
        return
    await callback.answer("Генерирую пост...")
    await callback.message.answer("⏳ Создаю черновик поста...")
    try:
        from bot.services.llm import generate_post
        from bot.services.publisher import send_draft_to_moderators
        classification = data["classification"]
        strategies = await queries.get_strategies_for_context(limit=5)
        post = await generate_post(
            label=classification.get("label", ""),
            summary_ru=classification.get("summary_ru", ""),
            entities=classification.get("entities", []),
            confidence_band="single_weak",
            strategies=strategies,
        )
        draft_id = await queries.create_draft(post.get("body_ru", ""), post.get("body_hy", ""))
        await send_draft_to_moderators(
            draft_id=draft_id, body_ru=post.get("body_ru", ""),
            body_hy=post.get("body_hy", ""), bot=callback.bot,
            tier_label="🔗 Ссылка", source_info=data["url"][:80],
            label=classification.get("label", ""),
            confidence=classification.get("confidence", 0),
        )
        await callback.message.answer(f"✅ Черновик #{draft_id} создан и отправлен на модерацию.")
    except Exception as e:
        await callback.message.answer(f"Ошибка: {e}")


@router.callback_query(F.data.startswith("url_obs:"))
async def handle_url_obsidian(callback: CallbackQuery) -> None:
    import bot.handlers.moderation as _self
    cache = getattr(_self, "_url_cache", {})
    key = int(callback.data.split(":")[1])
    data = cache.get(key)
    if not data:
        await callback.answer("Данные устарели.", show_alert=True)
        return
    try:
        from bot.services.obsidian import save_raw_to_obsidian
        import hashlib
        await save_raw_to_obsidian(
            source_type="url",
            source_url=data["url"],
            title=data["url"][:80],
            body=data["text"],
            classification=data["classification"],
            event_id=abs(hash(data["url"])) % 10**6,
        )
        await callback.answer("✅ Сохранено в Obsidian!", show_alert=True)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)




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
