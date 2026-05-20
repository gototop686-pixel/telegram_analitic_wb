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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.db import queries
from bot.services.publisher import publish_draft


class PipelineKwAdd(StatesGroup):
    waiting = State()


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
                [KeyboardButton(text="🔬 Пайплайн"), KeyboardButton(text="🔍 Поиск")],
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
    strategy_proposals = stats.get("strategy_proposals", 0)
    strat_line = f"\n💡 Стратегий на одобрение: <b>{strategy_proposals}</b>" if strategy_proposals else ""
    text = (
        "<b>📊 Статус бота</b>\n\n"
        f"📥 Всего событий в БД: <b>{stats['raw_total']}</b>\n"
        f"⏳ Не обработано: <b>{stats['unprocessed']}</b>\n"
        f"📝 Черновиков на проверке: <b>{stats['drafts_pending']}</b>\n"
        f"📢 Опубликовано постов: <b>{stats['published']}</b>\n"
        f"🔗 Активных источников: <b>{stats['sources']}</b>"
        f"{strat_line}"
    )
    admin_ids = await queries.get_admin_ids()
    is_admin = message.from_user.id in admin_ids
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Sync в Obsidian", callback_data="obsidian:sync"),
    ]]) if is_admin else None
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


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
        text="👁 Что в очереди (предпросмотр)",
        callback_data="process:preview",
    )])
    buttons.insert(1, [InlineKeyboardButton(
        text="🗑 Архивировать старше 7 дней",
        callback_data="process:archive",
    )])
    buttons.insert(2, [InlineKeyboardButton(
        text="🧹 Очистить нерелевантное (бесплатно)",
        callback_data="process:filter",
    )])
    buttons.append([InlineKeyboardButton(
        text="🌐 Обработать всё (до 30 событий)",
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

    if callback.data == "process:preview":
        await callback.answer()
        items = await queries.preview_unprocessed_titles(limit=30)
        if not items:
            await callback.message.answer("Очередь пуста.")
            return
        type_icons = {"telegram": "📱", "rss": "📡", "youtube": "▶️", "offer": "📄"}
        lines = [f"👁 <b>В очереди (последние {len(items)}):</b>\n"]
        for item in items:
            icon = type_icons.get(item["source_type"], "📌")
            title = item.get("title") or item.get("url") or "без названия"
            date = str(item.get("fetched_at", ""))[:10]
            src = item.get("source_id", "")
            lines.append(f"{icon} <i>{date}</i> {src}\n    {title[:80]}")
        await callback.message.answer(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    if callback.data == "process:archive":
        await callback.message.edit_text("🗑 Архивирую события старше 7 дней...")
        await callback.answer()
        async def _archive():
            try:
                count = await queries.archive_old_events(days=7)
                await callback.message.answer(
                    f"🗑 <b>Архивировано старых событий: {count}</b>\n\n"
                    "Они помечены как обработанные и больше не попадут в очередь.\n"
                    "Нажми <b>🤖 Обработать</b> чтобы увидеть актуальную очередь.",
                    parse_mode="HTML",
                )
            except Exception as e:
                await callback.message.answer(f"Ошибка: {e}")
        asyncio.create_task(_archive())
        return

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
            if count == 0:
                await callback.message.answer(
                    "⚠️ <b>Обработано 0 событий.</b>\n\n"
                    "Возможные причины:\n"
                    "• <code>GEMINI_API_KEY</code> не добавлен в Railway Variables\n"
                    "• Баланс Anthropic (Claude) исчерпан\n"
                    "• Все события отфильтрованы по ключевым словам\n\n"
                    "Добавь <code>GEMINI_API_KEY</code> в Railway → Variables.",
                    parse_mode="HTML",
                )
            else:
                await callback.message.answer(
                    f"✅ Обработано: <b>{count}</b> событий → опубликовано в каналы.",
                    parse_mode="HTML",
                )
        except Exception as e:
            await callback.message.answer(f"Ошибка: {e}")

    asyncio.create_task(_process())


@router.message(F.text == "⚙️ Управление")
async def kb_menu(message: Message) -> None:
    from bot.handlers.admin_menu import cmd_menu
    await cmd_menu(message)


async def _pipeline_text_and_kb() -> tuple[str, InlineKeyboardMarkup]:
    from bot.services.llm import DEEPSEEK_API_KEY, GEMINI_API_KEY

    def _icon(key): return "✅" if key else "❌"

    if DEEPSEEK_API_KEY:
        classify_model = "DeepSeek-V3"
        post_model = "DeepSeek-V3"
        cost_note = "~$0.001/пост"
    elif GEMINI_API_KEY:
        classify_model = "Gemini Flash"
        post_model = "Gemini Flash"
        cost_note = "бесплатно (лимит 1500/день)"
    else:
        classify_model = "Claude Haiku"
        post_model = "Claude Sonnet"
        cost_note = "платно $$"

    max_events = await queries.get_setting("max_events_per_run", "5")
    min_conf = await queries.get_setting("min_confidence", "0.45")

    core_raw = await queries.get_setting("filter_core_keywords", "")
    context_raw = await queries.get_setting("filter_context_keywords", "")
    from bot.handlers.admin_menu import _kw_list
    core_words = _kw_list(core_raw)
    context_words = _kw_list(context_raw)

    text = (
        f"🔬 <b>Пайплайн анализа новостей</b>\n\n"
        f"<b>🤖 Активные модели:</b>\n"
        f"├ Классификация: <b>{classify_model}</b>\n"
        f"├ Кластеризация: <b>{classify_model}</b>\n"
        f"├ Генерация поста: <b>{post_model}</b> ({cost_note})\n"
        f"└ Анализ оферты: <b>Claude Sonnet</b> (лучшее качество)\n\n"
        f"<b>🔑 API ключи:</b>\n"
        f"├ {_icon(DEEPSEEK_API_KEY)} DEEPSEEK_API_KEY\n"
        f"├ {_icon(GEMINI_API_KEY)} GEMINI_API_KEY\n"
        f"└ ✅ ANTHROPIC_API_KEY\n\n"
        f"<b>📊 Параметры обработки:</b>\n"
        f"├ Событий за запуск: <b>{max_events}</b>\n"
        f"├ Мин. уверенность: <b>{min_conf}</b>\n"
        f"└ Сбор новостей: каждые <b>2 часа</b>\n\n"
        f"<b>🔍 Ключевые слова:</b>\n"
        f"├ CORE: <b>{len(core_words)} слов</b> — 1 совпадение = проходит\n"
        f"└ CONTEXT: <b>{len(context_words)} слов</b> — нужно 2+\n\n"
        f"<b>Маршрут публикации:</b>\n"
        f"🇷🇺 RU → @GTTnews\n"
        f"🇦🇲 HY → @gttnewsam\n"
        f"🌍 both → оба канала\n"
        f"❓ unclear → черновики (ручная проверка)"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"🔑 CORE ({len(core_words)})", callback_data="pipe:kw:core"),
            InlineKeyboardButton(text=f"🔑 CONTEXT ({len(context_words)})", callback_data="pipe:kw:context"),
        ],
        [
            InlineKeyboardButton(text="📈 Порог 0.55 (строже)", callback_data="pipe_conf:0.55"),
            InlineKeyboardButton(text="📉 Порог 0.35 (мягче)", callback_data="pipe_conf:0.35"),
        ],
        [
            InlineKeyboardButton(text="📦 Пакет 3", callback_data="pipe_max:3"),
            InlineKeyboardButton(text="📦 Пакет 5", callback_data="pipe_max:5"),
            InlineKeyboardButton(text="📦 Пакет 10", callback_data="pipe_max:10"),
        ],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="pipe:refresh")],
    ])
    return text, kb


@router.message(F.text == "🔬 Пайплайн")
async def kb_pipeline(message: Message) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        return
    text, kb = await _pipeline_text_and_kb()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "pipe:refresh")
async def cb_pipe_refresh(call: CallbackQuery) -> None:
    admin_ids = await queries.get_admin_ids()
    if call.from_user.id not in admin_ids:
        await call.answer("Нет доступа")
        return
    text, kb = await _pipeline_text_and_kb()
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await call.answer("🔄 Обновлено")


@router.callback_query(F.data.startswith("pipe:kw:"))
async def cb_pipe_kw_view(call: CallbackQuery) -> None:
    admin_ids = await queries.get_admin_ids()
    if call.from_user.id not in admin_ids:
        await call.answer("Нет доступа")
        return
    kw_type = call.data.split(":")[2]  # core | context
    setting_key = f"filter_{kw_type}_keywords"
    raw = await queries.get_setting(setting_key, "")
    from bot.handlers.admin_menu import _kw_list
    words = _kw_list(raw)
    label = "CORE" if kw_type == "core" else "CONTEXT"

    buttons = []
    for i, w in enumerate(words):
        buttons.append([InlineKeyboardButton(
            text=f"❌ {w}",
            callback_data=f"pipe:kw:del:{kw_type}:{i}",
        )])
    buttons.append([InlineKeyboardButton(text=f"➕ Добавить слово", callback_data=f"pipe:kw:add:{kw_type}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к пайплайну", callback_data="pipe:refresh")])

    hint = "1 совпадение = новость проходит фильтр" if kw_type == "core" else "нужно 2+ совпадения (если нет CORE)"
    await call.message.edit_text(
        f"<b>🔑 {label} ключевые слова ({len(words)})</b>\n"
        f"<i>{hint}</i>\n\n"
        + ("\n".join(f"• <code>{w}</code>" for w in words) if words else "⚠️ Список пуст — используются слова по умолчанию")
        + "\n\nНажми ❌ рядом со словом чтобы удалить его.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await call.answer()


@router.callback_query(F.data.startswith("pipe:kw:del:"))
async def cb_pipe_kw_del(call: CallbackQuery) -> None:
    admin_ids = await queries.get_admin_ids()
    if call.from_user.id not in admin_ids:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":")  # pipe:kw:del:core:0
    kw_type, idx_str = parts[3], parts[4]
    setting_key = f"filter_{kw_type}_keywords"
    raw = await queries.get_setting(setting_key, "")
    from bot.handlers.admin_menu import _kw_list
    words = _kw_list(raw)
    idx = int(idx_str)
    if 0 <= idx < len(words):
        removed = words.pop(idx)
        await queries.set_setting(setting_key, ",".join(words))
        import bot.services.ingestion as ing
        ing._cached_core_kw = None
        await call.answer(f"✅ Удалено: «{removed}»", show_alert=True)
    # Refresh keyword list
    call.data = f"pipe:kw:{kw_type}"
    await cb_pipe_kw_view(call)


@router.callback_query(F.data.startswith("pipe:kw:add:"))
async def cb_pipe_kw_add_start(call: CallbackQuery, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if call.from_user.id not in admin_ids:
        await call.answer("Нет доступа", show_alert=True)
        return
    kw_type = call.data.split(":")[3]
    label = "CORE" if kw_type == "core" else "CONTEXT"
    await state.set_state(PipelineKwAdd.waiting)
    await state.update_data(kw_type=kw_type)
    await call.message.edit_text(
        f"<b>➕ Добавить {label} ключевое слово</b>\n\n"
        f"Введи слово или фразу (строчными буквами).\n"
        f"Несколько сразу — через запятую: <code>wildberries, wb, вайлдберриз</code>\n\n"
        f"Отправь /cancel для отмены.",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(PipelineKwAdd.waiting)
async def cb_pipe_kw_add_save(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("Отменено.")
        return

    data = await state.get_data()
    kw_type = data.get("kw_type", "core")
    setting_key = f"filter_{kw_type}_keywords"
    raw = await queries.get_setting(setting_key, "")
    from bot.handlers.admin_menu import _kw_list
    words = _kw_list(raw)

    new_words = [w.strip().lower() for w in (message.text or "").split(",") if w.strip()]
    added = []
    for w in new_words:
        if w and w not in words:
            words.append(w)
            added.append(w)

    await queries.set_setting(setting_key, ",".join(words))
    import bot.services.ingestion as ing
    ing._cached_core_kw = None
    await state.clear()

    label = "CORE" if kw_type == "core" else "CONTEXT"
    if added:
        await message.answer(
            f"✅ Добавлено в <b>{label}</b>: {', '.join(f'<code>{w}</code>' for w in added)}\n"
            f"Всего слов: <b>{len(words)}</b>",
            parse_mode="HTML",
        )
    else:
        await message.answer("⚠️ Слова уже есть в списке или ввод пустой.")

    # Show updated pipeline panel
    text, kb = await _pipeline_text_and_kb()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(F.text == "📄 Анализ оферты")
async def kb_offer(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    offers = await queries.list_stored_offers()
    locale_names = {"ru": "🇷🇺 Россия", "hy": "🇦🇲 Армения", "kz": "🇰🇿 Казахстан"}
    lines = ["<b>📄 Оферты Wildberries</b>\n"]
    if offers:
        for o in offers:
            locale_label = locale_names.get(o["locale"], o["locale"].upper())
            date = str(o.get("uploaded_at", ""))[:10]
            lines.append(f"{locale_label}: <b>{o['filename']}</b> ({date})")
    else:
        lines.append("⚠️ Оферты ещё не загружены.")
    lines.append("\nЧтобы загрузить или обновить оферту — нажми кнопку и отправь PDF.")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Загрузить RU оферту", callback_data="offer_upload:ru"),
                InlineKeyboardButton(text="🇦🇲 Загрузить HY оферту", callback_data="offer_upload:hy"),
            ],
            [
                InlineKeyboardButton(text="🔄 Сравнить RU версии", callback_data="offer_compare:ru"),
                InlineKeyboardButton(text="🔄 Сравнить HY версии", callback_data="offer_compare:hy"),
            ],
        ]),
    )


@router.message(F.text == "📝 Черновики")
async def kb_drafts(message: Message) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return
    drafts = await queries.get_pending_drafts(limit=1)
    total_rows = await queries.get_pending_drafts(limit=100)
    total = len(total_rows)
    if total == 0:
        await message.answer("✅ Нет черновиков на проверке.")
        return
    await message.answer(
        f"📝 <b>Черновиков на проверке: {total}</b>\n\nЧто сделать?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Показать все", callback_data="drafts:show")],
            [InlineKeyboardButton(text="❌ Отклонить все", callback_data="drafts:reject_all")],
        ]),
    )


@router.callback_query(F.data == "drafts:show")
async def drafts_show(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_pending_drafts(callback.message)


@router.callback_query(F.data == "drafts:reject_all")
async def drafts_reject_all(callback: CallbackQuery) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if callback.from_user.id not in moderator_ids:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    drafts = await queries.get_pending_drafts(limit=100)
    count = 0
    for d in drafts:
        await queries.reject_draft(d["id"])
        count += 1
    await callback.message.edit_text(
        f"❌ Отклонено черновиков: <b>{count}</b>",
        parse_mode="HTML",
    )
    await callback.answer()


def _strip_html(text: str) -> str:
    import re
    return re.sub(r'<[^>]+>', '', text or '')


async def show_pending_drafts(message: Message) -> None:
    drafts = await queries.get_pending_drafts(limit=10)
    if not drafts:
        await message.answer("✅ Нет черновиков на проверке.")
        return
    await message.answer(f"📝 <b>Черновики на проверке: {len(drafts)}</b>", parse_mode="HTML")
    for draft in drafts:
        # Strip HTML before slicing to avoid broken tags
        body_ru = _strip_html(draft.get("body_ru", ""))
        body_hy = _strip_html(draft.get("body_hy", ""))
        created = str(draft.get("created_at", ""))[:16]
        preview = (
            f"📋 <b>Черновик #{draft['id']}</b> · {created}\n\n"
            f"🇷🇺 RU:\n{body_ru[:600]}\n\n"
            f"🇦🇲 HY:\n{body_hy[:300] if body_hy else '—'}"
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
    waiting_pdf = State()


@router.callback_query(F.data.startswith("offer_upload:"))
async def handle_offer_upload_btn(callback: CallbackQuery, state: FSMContext) -> None:
    locale = callback.data.split(":")[1]
    locale_name = {"ru": "🇷🇺 Россия", "hy": "🇦🇲 Армения", "kz": "🇰🇿 Казахстан"}.get(locale, locale.upper())
    await state.update_data(offer_locale=locale)
    await state.set_state(OfferFSM.waiting_pdf)
    await callback.message.answer(
        f"Отправь PDF-файл оферты WB · {locale_name}\n\n"
        "Старая версия будет заменена автоматически.",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("offer_compare:"))
async def handle_offer_compare_btn(callback: CallbackQuery) -> None:
    locale = callback.data.split(":")[1]
    locale_name = {"ru": "🇷🇺 Россия", "hy": "🇦🇲 Армения"}.get(locale, locale.upper())
    existing = await queries.get_stored_offer(locale)
    if not existing:
        await callback.answer(f"Оферта {locale_name} ещё не загружена.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"Отправь новую версию оферты {locale_name} для сравнения.\n"
        "Старая при этом НЕ заменится — только сравним.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="offer_compare_cancel"),
        ]]),
    )
    await callback.message.answer(
        f"<i>Текущая версия: {existing['filename']} ({str(existing.get('uploaded_at',''))[:10]})</i>",
        parse_mode="HTML",
    )
    import bot.handlers.moderation as _self
    if not hasattr(_self, "_offer_compare_pending"):
        _self._offer_compare_pending = {}
    _self._offer_compare_pending[callback.from_user.id] = locale


@router.callback_query(F.data == "offer_compare_cancel")
async def handle_offer_compare_cancel(callback: CallbackQuery) -> None:
    import bot.handlers.moderation as _self
    if hasattr(_self, "_offer_compare_pending"):
        _self._offer_compare_pending.pop(callback.from_user.id, None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отменено.")


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext) -> None:
    moderator_ids = await queries.get_moderator_ids()
    if message.from_user.id not in moderator_ids:
        return

    # Check if user is in compare mode (no FSM state, but compare pending)
    import bot.handlers.moderation as _self
    compare_locale = getattr(_self, "_offer_compare_pending", {}).get(message.from_user.id)
    if compare_locale:
        _self._offer_compare_pending.pop(message.from_user.id, None)
        await message.answer("⏳ Извлекаю текст...")
        text = await _extract_text_from_doc(message)
        if not text or len(text.strip()) < 50:
            await message.answer("Не удалось извлечь текст из документа.")
            return
        existing = await queries.get_stored_offer(compare_locale)
        old_text = existing["text_content"] if existing else ""
        await message.answer("🔄 Сравниваю версии через Claude Sonnet...")
        await _do_offer_compare(message, old_text, text)
        return

    # Check FSM state for offer upload
    data = await state.get_data()
    locale = data.get("offer_locale")
    current_state = await state.get_state()

    if current_state == OfferFSM.waiting_pdf and locale:
        await state.clear()
        await message.answer("⏳ Скачиваю документ...")
        text = await _extract_text_from_doc(message)
        if not text or len(text.strip()) < 50:
            await message.answer("Не удалось извлечь текст из документа.")
            return
        filename = message.document.file_name or "document"
        locale_name = {"ru": "🇷🇺 Россия", "hy": "🇦🇲 Армения", "kz": "🇰🇿 Казахстан"}.get(locale, locale.upper())

        # Save to DB (replaces old automatically via ON CONFLICT)
        await queries.save_stored_offer(locale, text, filename, message.from_user.id, filename)

        # Save to Obsidian
        try:
            from bot.services.obsidian import save_offer_to_obsidian
            await save_offer_to_obsidian(locale, filename, text)
        except Exception as e:
            print(f"[offer] Obsidian save failed: {e}")

        await message.answer(
            f"✅ <b>Оферта WB · {locale_name} обновлена</b>\n\n"
            f"📄 Файл: {filename}\n"
            f"💾 Сохранена в базу и Obsidian.\n\n"
            f"Теперь бот будет сверять данные с этой версией при анализе новостей.",
            parse_mode="HTML",
        )
        return

    # No offer context — treat as general document analysis
    await message.answer("⏳ Скачиваю документ...")
    text = await _extract_text_from_doc(message)
    if not text or len(text.strip()) < 50:
        await message.answer("Не удалось извлечь текст из документа.")
        return
    filename = message.document.file_name or "document"
    await message.answer(
        f"📄 Документ получен: <b>{filename}</b>\n\nЧто сделать?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Сохранить как RU оферту", callback_data="offer_save_doc:ru"),
                InlineKeyboardButton(text="🇦🇲 Сохранить как HY оферту", callback_data="offer_save_doc:hy"),
            ],
            [InlineKeyboardButton(text="📊 Только анализ", callback_data="offer_save_doc:analyze")],
        ]),
    )
    import bot.handlers.moderation as _self2
    if not hasattr(_self2, "_doc_cache"):
        _self2._doc_cache = {}
    _self2._doc_cache[message.from_user.id] = {"text": text, "filename": filename}


@router.callback_query(F.data.startswith("offer_save_doc:"))
async def handle_offer_save_doc(callback: CallbackQuery) -> None:
    locale = callback.data.split(":")[1]
    import bot.handlers.moderation as _self
    data = getattr(_self, "_doc_cache", {}).get(callback.from_user.id)
    if not data:
        await callback.answer("Данные устарели. Отправь документ ещё раз.", show_alert=True)
        return
    text = data["text"]
    filename = data["filename"]

    if locale == "analyze":
        await callback.message.edit_text("🤖 Анализирую через Claude Sonnet...")
        await _do_offer_analysis(callback.message, text, filename, callback.from_user.id)
        await callback.answer()
        return

    locale_name = {"ru": "🇷🇺 Россия", "hy": "🇦🇲 Армения"}.get(locale, locale.upper())
    await queries.save_stored_offer(locale, text, filename, callback.from_user.id, filename)
    try:
        from bot.services.obsidian import save_offer_to_obsidian
        await save_offer_to_obsidian(locale, filename, text)
    except Exception as e:
        print(f"[offer] Obsidian save failed: {e}")

    await callback.message.edit_text(
        f"✅ Оферта WB · {locale_name} сохранена: <b>{filename}</b>",
        parse_mode="HTML",
    )
    await callback.answer()


async def _do_offer_compare(message: Message, old_text: str, new_text: str) -> None:
    try:
        from bot.services.llm import compare_offers
        result = await compare_offers(old_text, new_text)
        urgency_emoji = "🔴" if result.get("urgency") == 1 else "🟡"
        if not result.get("has_changes"):
            await message.answer("✅ Документы идентичны — изменений не обнаружено.")
            return
        parts = [f"{urgency_emoji} <b>Сравнение версий оферты WB</b>\n"]
        critical = "\n".join(f"  ⚠️ {x}" for x in result.get("critical_changes", []))
        numbers = "\n".join(f"  💰 {x}" for x in result.get("numbers_changed", []))
        changed = "\n".join(f"  🔄 {x}" for x in result.get("changed", []))
        added = "\n".join(f"  ✅ {x}" for x in result.get("added", []))
        removed = "\n".join(f"  ❌ {x}" for x in result.get("removed", []))
        if critical:
            parts.append(f"<b>🚨 Критично:</b>\n{critical}")
        if numbers:
            parts.append(f"<b>💰 Цифры:</b>\n{numbers}")
        if changed:
            parts.append(f"<b>🔄 Изменено:</b>\n{changed}")
        if added:
            parts.append(f"<b>✅ Добавлено:</b>\n{added}")
        if removed:
            parts.append(f"<b>❌ Удалено:</b>\n{removed}")
        parts.append(f"<b>Резюме:</b> {result.get('summary_ru', '—')}")
        await message.answer("\n\n".join(parts), parse_mode="HTML")
        if result.get("urgency") == 1:
            content_hash = hashlib.sha256(new_text.encode()).hexdigest()
            await message.answer(
                "⚠️ Критичные изменения! Создать пост?",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Создать пост", callback_data=f"offer_post:{content_hash[:16]}"),
                    InlineKeyboardButton(text="❌ Не надо", callback_data="offer_skip"),
                ]]),
            )
    except Exception as e:
        await message.answer(f"Ошибка сравнения: {e}")


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


@router.message(Command("test_gemini"))
async def cmd_test_gemini(message: Message) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        return
    await message.answer("🔍 Тестирую Gemini API...")
    try:
        from bot.services.llm import _gemini, GEMINI_API_KEY, GEMINI_MODEL
        if not GEMINI_API_KEY:
            await message.answer("❌ <code>GEMINI_API_KEY</code> не установлен в Railway Variables.", parse_mode="HTML")
            return
        result = await _gemini("Скажи 'OK' одним словом.", max_tokens=10)
        await message.answer(
            f"✅ <b>Gemini работает!</b>\n\n"
            f"Модель: <code>{GEMINI_MODEL}</code>\n"
            f"Ответ: <code>{result[:100]}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка Gemini:</b>\n<code>{e}</code>\n\n"
            f"Проверь:\n"
            f"• Правильно ли скопирован ключ в Railway\n"
            f"• Ключ должен начинаться с <code>AIza</code>",
            parse_mode="HTML",
        )


@router.message(Command("test_deepseek"))
async def cmd_test_deepseek(message: Message) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        return
    await message.answer("🔍 Тестирую DeepSeek API...")
    try:
        from bot.services.llm import _deepseek, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        if not DEEPSEEK_API_KEY:
            await message.answer(
                "❌ <code>DEEPSEEK_API_KEY</code> не установлен в Railway Variables.\n\n"
                "Получи ключ на <b>platform.deepseek.com</b> → API Keys",
                parse_mode="HTML",
            )
            return
        result = await _deepseek("Скажи 'OK' одним словом.", max_tokens=10)
        await message.answer(
            f"✅ <b>DeepSeek работает!</b>\n\n"
            f"Модель: <code>{DEEPSEEK_MODEL}</code>\n"
            f"Ответ: <code>{result[:100]}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка DeepSeek:</b>\n<code>{e}</code>\n\n"
            f"Проверь:\n"
            f"• Правильно ли скопирован ключ в Railway\n"
            f"• Ключ должен начинаться с <code>sk-</code>\n"
            f"• Есть ли баланс на platform.deepseek.com",
            parse_mode="HTML",
        )


@router.message(Command("pipeline"))
async def cmd_pipeline(message: Message) -> None:
    """Show analysis pipeline: which model does what + current thresholds."""
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        return

    from bot.services.llm import DEEPSEEK_API_KEY, GEMINI_API_KEY, DEEPSEEK_MODEL, GEMINI_MODEL

    def model_status(name: str, key: str) -> str:
        return f"✅ <b>{name}</b>" if key else f"❌ {name} (ключ не задан)"

    # Determine active model per task
    if DEEPSEEK_API_KEY:
        classify_model = f"✅ DeepSeek-V3 (активен)"
        post_model = f"✅ DeepSeek-V3 (активен)"
    elif GEMINI_API_KEY:
        classify_model = f"🟡 Gemini Flash (DeepSeek не задан)"
        post_model = f"🟡 Gemini Flash (DeepSeek не задан)"
    else:
        classify_model = "🔴 Claude Haiku (платный, оба ключа не заданы)"
        post_model = "🔴 Claude Sonnet (платный, оба ключа не заданы)"

    # Load current settings
    max_events = await queries.get_setting("max_events_per_run", "5")
    min_conf = await queries.get_setting("min_confidence", "0.45")
    ingest_kw = await queries.get_setting("filter_core_keywords", "(по умолчанию)")
    context_kw = await queries.get_setting("filter_context_keywords", "(по умолчанию)")

    text = (
        f"⚙️ <b>Пайплайн анализа новостей</b>\n\n"
        f"<b>🤖 Модели:</b>\n"
        f"├ Классификация: {classify_model}\n"
        f"├ Кластеризация: {classify_model}\n"
        f"├ Генерация поста: {post_model}\n"
        f"└ Анализ оферты: ✅ Claude Sonnet (всегда)\n\n"
        f"<b>🔑 API ключи:</b>\n"
        f"├ {model_status('DEEPSEEK_API_KEY', DEEPSEEK_API_KEY)}\n"
        f"├ {model_status('GEMINI_API_KEY', GEMINI_API_KEY)}\n"
        f"└ ANTHROPIC_API_KEY: всегда задан\n\n"
        f"<b>📊 Параметры обработки:</b>\n"
        f"├ Событий за запуск: <code>{max_events}</code>\n"
        f"├ Мин. уверенность: <code>{min_conf}</code> (0.0–1.0)\n"
        f"├ Сбор новостей: каждые 2 часа\n"
        f"└ Дневной дайджест: 09:00 МСК\n\n"
        f"<b>🔍 Ключевые слова фильтрации:</b>\n"
        f"└ Основные: <code>{ingest_kw[:80]}</code>\n\n"
        f"<b>Этапы обработки новости:</b>\n"
        f"1️⃣ Сбор RSS/Google News (каждые 2ч)\n"
        f"2️⃣ Keyword-фильтр при сохранении\n"
        f"3️⃣ Keyword-фильтр при обработке\n"
        f"4️⃣ Кластеризация похожих новостей\n"
        f"5️⃣ Классификация + резюме (DeepSeek)\n"
        f"6️⃣ Генерация поста (DeepSeek)\n"
        f"7️⃣ Автопубликация по рынку (RU/HY/both)"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📈 Поднять порог (0.55)", callback_data="pipe_conf:0.55"),
            InlineKeyboardButton(text="📉 Снизить порог (0.35)", callback_data="pipe_conf:0.35"),
        ],
        [
            InlineKeyboardButton(text="📦 Пакет 3 события", callback_data="pipe_max:3"),
            InlineKeyboardButton(text="📦 Пакет 10 событий", callback_data="pipe_max:10"),
        ],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="pipe_refresh")],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("pipe_conf:"))
async def cb_pipe_conf(call: CallbackQuery) -> None:
    admin_ids = await queries.get_admin_ids()
    if call.from_user.id not in admin_ids:
        await call.answer("Нет доступа")
        return
    val = call.data.split(":")[1]
    await queries.set_setting("min_confidence", val)
    await call.answer(f"✅ Порог уверенности → {val}")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ <code>min_confidence</code> = <b>{val}</b>\n\nНовости с уверенностью ниже {val} будут пропускаться.", parse_mode="HTML")


@router.callback_query(F.data.startswith("pipe_max:"))
async def cb_pipe_max(call: CallbackQuery) -> None:
    admin_ids = await queries.get_admin_ids()
    if call.from_user.id not in admin_ids:
        await call.answer("Нет доступа")
        return
    val = call.data.split(":")[1]
    await queries.set_setting("max_events_per_run", val)
    await call.answer(f"✅ Размер пакета → {val}")
    await call.message.answer(f"✅ <code>max_events_per_run</code> = <b>{val}</b>", parse_mode="HTML")


@router.callback_query(F.data == "pipe_refresh")
async def cb_pipe_refresh(call: CallbackQuery) -> None:
    admin_ids = await queries.get_admin_ids()
    if call.from_user.id not in admin_ids:
        await call.answer("Нет доступа")
        return
    await call.answer("🔄 Обновляю...")
    await call.message.delete()
    await cmd_pipeline(call.message)


@router.message(Command("dbcheck"))
async def cmd_dbcheck(message: Message) -> None:
    """Check DB schema — verify all migrations ran."""
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        return
    from bot.db.pool import get_pool
    pool = await get_pool()
    checks = []

    # Check tables exist
    for table in ["publishes", "strategy_proposals", "stored_offers", "strategies", "raw_events", "drafts"]:
        row = await pool.fetchrow(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=$1)", table
        )
        exists = row[0]
        checks.append(f"{'✅' if exists else '❌'} таблица <code>{table}</code>")

    # Check key columns
    col_checks = [
        ("publishes", "obsidian_path"),
        ("publishes", "market"),
        ("publishes", "tg_message_id"),
    ]
    for tbl, col in col_checks:
        row = await pool.fetchrow(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name=$2)",
            tbl, col
        )
        exists = row[0]
        checks.append(f"{'✅' if exists else '❌'} колонка <code>{tbl}.{col}</code>")

    # Check GEMINI key
    import os
    gemini_ok = bool(os.environ.get("GEMINI_API_KEY"))
    anthropic_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    checks.append(f"{'✅' if gemini_ok else '❌'} <code>GEMINI_API_KEY</code>")
    checks.append(f"{'✅' if anthropic_ok else '❌'} <code>ANTHROPIC_API_KEY</code>")

    # Count channels
    channels = await queries.get_publish_channels_full()
    checks.append(f"✅ Каналов публикации: <b>{len(channels)}</b>")

    text = "<b>🔍 Диагностика БД и окружения</b>\n\n" + "\n".join(checks)
    # Recommendations
    missing = [c for c in checks if c.startswith("❌")]
    if missing:
        text += "\n\n⚠️ <b>Нужно исправить:</b>"
        if any("strategy_proposals" in c for c in missing):
            text += "\n• Запусти миграцию 007 в Railway PostgreSQL"
        if any("obsidian_path" in c or "market" in c for c in missing):
            text += "\n• Запусти миграцию 007 в Railway PostgreSQL"
        if any("GEMINI" in c for c in missing):
            text += "\n• Добавь <code>GEMINI_API_KEY</code> в Railway Variables"
    else:
        text += "\n\n✅ <b>Всё в порядке!</b>"

    await message.answer(text, parse_mode="HTML")


@router.message(Command("check_obsidian"))
async def cmd_check_obsidian(message: Message) -> None:
    """Diagnose Obsidian GitHub connection."""
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await message.answer("Только для администраторов.")
        return
    await message.answer("🔍 Проверяю подключение к GitHub...")
    try:
        from bot.services.obsidian import check_obsidian_connection
        result = await check_obsidian_connection()
        if result["ok"]:
            privacy = "приватный 🔒" if result.get("private") else "публичный 🌐"
            await message.answer(
                f"✅ <b>Подключение работает!</b>\n\n"
                f"👤 Аккаунт: <code>{result['login']}</code>\n"
                f"📁 Репо: <code>{result['repo']}</code> ({privacy})",
                parse_mode="HTML",
            )
        else:
            login_line = f"\n👤 Аккаунт: <code>{result['login']}</code>" if result.get("login") else ""
            await message.answer(
                f"❌ <b>Ошибка подключения</b>{login_line}\n\n"
                f"{result['error']}\n\n"
                f"<b>Что проверить в Railway Variables:</b>\n"
                f"• <code>OBSIDIAN_GITHUB_TOKEN</code> — токен GitHub (классический, с правами <b>repo</b>)\n"
                f"• <code>OBSIDIAN_GITHUB_REPO</code> — название репо в формате <b>username/repo-name</b>",
                parse_mode="HTML",
            )
    except Exception as e:
        await message.answer(f"Ошибка при проверке: {e}")


@router.message(Command("setup_notes"))
async def cmd_setup_notes(message: Message) -> None:
    """Push project architecture & env vars reference to Obsidian."""
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await message.answer("Только для администраторов.")
        return
    await message.answer("⏳ Сохраняю документацию в Obsidian...")
    try:
        from bot.services.obsidian import push_project_setup_notes
        ok = await push_project_setup_notes()
        if ok:
            await message.answer(
                "✅ Документация сохранена в Obsidian:\n"
                "<code>Настройки/Архитектура_бота.md</code>\n\n"
                "Там есть: архитектура, таблицы БД, список переменных Railway, модели Claude.",
                parse_mode="HTML",
            )
        else:
            await message.answer("⚠️ Не удалось сохранить — проверь OBSIDIAN_GITHUB_TOKEN и OBSIDIAN_GITHUB_REPO.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


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




@router.callback_query(F.data.startswith("ch_del:"))
async def handle_channel_delete(callback: CallbackQuery) -> None:
    """Delete post from all Telegram channels + Obsidian, mark draft rejected."""
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return

    draft_id = int(callback.data.split(":")[1])
    await callback.answer("Удаляю...")

    publish_records = await queries.get_publish_records(draft_id)
    deleted_tg = 0
    obsidian_path = ""

    for rec in publish_records:
        channel_id = rec.get("channel_id")
        tg_message_id = rec.get("tg_message_id")
        if not obsidian_path:
            obsidian_path = rec.get("obsidian_path", "")
        if channel_id and tg_message_id:
            try:
                await callback.bot.delete_message(channel_id, tg_message_id)
                deleted_tg += 1
            except Exception as e:
                print(f"[ch_del] Cannot delete TG msg {tg_message_id} in {channel_id}: {e}")

    if obsidian_path:
        try:
            from bot.services.obsidian import delete_from_obsidian
            await delete_from_obsidian(obsidian_path)
        except Exception as e:
            print(f"[ch_del] Obsidian delete failed: {e}")

    await queries.reject_draft(draft_id)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"🗑 Черновик #{draft_id} удалён из {deleted_tg} каналов"
        + (f" и Obsidian ({obsidian_path})" if obsidian_path else "") + ".",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "obsidian:sync")
async def handle_obsidian_sync(callback: CallbackQuery) -> None:
    """Sync published posts that are missing from Obsidian."""
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    await callback.answer("Запускаю синхронизацию...")
    await callback.message.answer("⏳ Синхронизирую опубликованные посты с Obsidian...")

    async def _sync():
        try:
            from bot.services.obsidian import save_published_to_obsidian
            records = await queries.get_published_without_obsidian(limit=50)
            if not records:
                await callback.message.answer("✅ Все опубликованные посты уже есть в Obsidian.")
                return
            synced = 0
            for rec in records:
                try:
                    body_ru = rec.get("body_ru", "") or ""
                    body_hy = rec.get("body_hy", "") or ""
                    market = rec.get("market", "") or "unclear"
                    draft_id = rec["draft_id"]
                    path = await save_published_to_obsidian(
                        draft_id=draft_id,
                        body_ru=body_ru,
                        body_hy=body_hy,
                        label="Синхронизация",
                        market=market,
                    )
                    await queries.mark_publish_obsidian_path(draft_id, path)
                    synced += 1
                except Exception as e:
                    print(f"[sync] draft {rec.get('draft_id')}: {e}")
            await callback.message.answer(
                f"✅ <b>Синхронизация завершена</b>\n\n"
                f"📤 Загружено в Obsidian: <b>{synced}</b> постов\n"
                f"📁 Папки: WB_Россия / WB_Армения / WB_ЕАЭС",
                parse_mode="HTML",
            )
        except Exception as e:
            await callback.message.answer(f"Ошибка синхронизации: {e}")

    asyncio.create_task(_sync())


@router.callback_query(F.data.startswith("strat_ok:"))
async def handle_strategy_approve(callback: CallbackQuery) -> None:
    """Approve strategy proposal → save to strategies table + Obsidian."""
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return

    proposal_id = int(callback.data.split(":")[1])
    proposal = await queries.approve_strategy_proposal(proposal_id)
    if not proposal:
        await callback.answer("Предложение не найдено.", show_alert=True)
        return

    # Insert into strategies table so it becomes available as RAG context
    strategy_id = await queries.save_strategy(
        title=proposal["title"],
        body=proposal["body"],
        category=proposal.get("category", "general"),
        created_by=callback.from_user.id,
    )

    # Save approved strategy to Obsidian
    try:
        from bot.services.obsidian import save_strategy_to_obsidian
        await save_strategy_to_obsidian(
            title=proposal["title"],
            body=proposal["body"],
            category=proposal.get("category", "general"),
            strategy_id=strategy_id,
        )
    except Exception as e:
        print(f"[strat_ok] Obsidian save failed: {e}")

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ Стратегия добавлена в базу!",
        reply_markup=None,
    )
    await callback.answer("Стратегия одобрена!")


@router.callback_query(F.data.startswith("strat_no:"))
async def handle_strategy_reject(callback: CallbackQuery) -> None:
    """Reject strategy proposal."""
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return

    proposal_id = int(callback.data.split(":")[1])
    await queries.reject_strategy_proposal(proposal_id)
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ Отклонено.",
        reply_markup=None,
    )
    await callback.answer("Отклонено.")


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
