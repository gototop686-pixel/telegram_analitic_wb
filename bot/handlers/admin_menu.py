import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.db import queries

router = Router()


class AdminFSM(StatesGroup):
    waiting_tg_channel = State()
    waiting_rss_url = State()
    waiting_publish_channel_id = State()
    waiting_publish_channel_locale = State()
    waiting_drafts_chat_id = State()
    waiting_prompt_key = State()
    waiting_prompt_value = State()
    waiting_keyword_add = State()       # data: kw_type = 'core' | 'context'
    waiting_strategy_title = State()
    waiting_strategy_body = State()     # data: strategy_title, strategy_category


# ── Main menu ──────────────────────────────────────────────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
            InlineKeyboardButton(text="🔗 Источники", callback_data="menu:sources"),
        ],
        [
            InlineKeyboardButton(text="📢 Каналы публикации", callback_data="menu:channels"),
            InlineKeyboardButton(text="🤖 Промпты Claude", callback_data="menu:prompts"),
        ],
        [
            InlineKeyboardButton(text="🔑 Ключевые слова", callback_data="menu:keywords"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
        ],
        [
            InlineKeyboardButton(text="🧠 Стратегии GoToTop", callback_data="menu:strategies"),
        ],
        [
            InlineKeyboardButton(text="🎛 Фильтры и модели", callback_data="menu:pipeline"),
        ],
    ])


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await message.answer("Только для администраторов.")
        return
    await message.answer(
        "<b>⚙️ Панель управления GoToTop Analytics</b>\n\nВыбери раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


def back_kb(target: str = "main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"menu:{target}"),
    ]])


# ── Stats ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:stats")
async def menu_stats(callback: CallbackQuery) -> None:
    stats = await queries.get_bot_stats()
    cost = await queries.get_llm_cost_total()
    text = (
        "<b>📊 Статистика бота</b>\n\n"
        f"📥 Событий в БД: <b>{stats['raw_total']}</b>\n"
        f"⏳ Не обработано: <b>{stats['unprocessed']}</b>\n"
        f"📝 Черновиков на проверке: <b>{stats['drafts_pending']}</b>\n"
        f"📢 Опубликовано постов: <b>{stats['published']}</b>\n"
        f"🔗 Активных источников: <b>{stats['sources']}</b>\n"
        f"💰 Потрачено на Claude: <b>${cost:.4f}</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb())
    await callback.answer()


# ── Sources ────────────────────────────────────────────────────────────────

def sources_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Telegram каналы", callback_data="src:list:telegram")],
        [InlineKeyboardButton(text="📋 RSS источники", callback_data="src:list:rss")],
        [InlineKeyboardButton(text="➕ Добавить TG канал", callback_data="src:add:telegram")],
        [InlineKeyboardButton(text="➕ Добавить RSS/URL", callback_data="src:add:rss")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")],
    ])


@router.callback_query(F.data == "menu:sources")
async def menu_sources(callback: CallbackQuery) -> None:
    rows = await queries.get_sources_by_type()
    lines = ["<b>🔗 Источники мониторинга</b>\n"]
    for r in rows:
        lines.append(f"• {r['source_type'].upper()} [{r['locale']}]: {r['cnt']} шт.")
    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=sources_menu_kb()
    )
    await callback.answer()


# ── Pipeline (Фильтры и модели) ────────────────────────────────────────────

@router.callback_query(F.data == "menu:pipeline")
async def menu_pipeline(callback: CallbackQuery) -> None:
    from bot.handlers.moderation import _pipeline_text_and_kb
    text, kb = await _pipeline_text_and_kb()
    # Add back button
    from aiogram.types import InlineKeyboardButton as IKB
    kb.inline_keyboard.append([IKB(text="◀️ Назад", callback_data="menu:main")])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("src:list:"))
async def src_list(callback: CallbackQuery) -> None:
    source_type = callback.data.split(":")[2]
    sources = await queries.get_sources_paginated(source_type=source_type, limit=15)
    if not sources:
        await callback.answer(f"Нет источников типа {source_type}", show_alert=True)
        return
    lines = [f"<b>Источники {source_type.upper()}:</b>\n"]
    buttons = []
    for s in sources:
        lines.append(f"• [{s['locale']}] {s['identifier']}")
        buttons.append([InlineKeyboardButton(
            text=f"❌ {s['identifier'][:30]}",
            callback_data=f"src:del:{s['id']}",
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:sources")])
    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("src:del:"))
async def src_delete(callback: CallbackQuery) -> None:
    source_id = int(callback.data.split(":")[2])
    await queries.deactivate_source(source_id)
    await callback.answer("✅ Источник отключён.", show_alert=True)
    await menu_sources(callback)


@router.callback_query(F.data == "src:add:telegram")
async def src_add_tg(callback: CallbackQuery, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    await state.set_state(AdminFSM.waiting_tg_channel)
    await callback.message.edit_text(
        "Введи username TG канала для мониторинга.\n"
        "Формат: <code>@channelname</code> или <code>channelname</code>\n\n"
        "Также укажи язык через пробел: <code>@channel ru</code>\n\n"
        "Отправь /cancel для отмены.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFSM.waiting_tg_channel)
async def fsm_add_tg(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb())
        return
    parts = message.text.strip().split()
    identifier = parts[0] if parts[0].startswith("@") else f"@{parts[0]}"
    locale = parts[1] if len(parts) > 1 and parts[1] in ("ru", "hy", "any") else "ru"
    ok = await queries.add_source("telegram", "media", locale, identifier)
    await state.clear()
    if ok:
        await message.answer(f"✅ Добавлен: {identifier} [{locale}]", reply_markup=main_menu_kb())
    else:
        await message.answer(f"⚠️ Уже существует или ошибка: {identifier}", reply_markup=main_menu_kb())


@router.callback_query(F.data == "src:add:rss")
async def src_add_rss(callback: CallbackQuery, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    await state.set_state(AdminFSM.waiting_rss_url)
    await callback.message.edit_text(
        "Введи URL RSS-ленты и язык.\n"
        "Формат: <code>https://example.com/rss ru</code>\n\n"
        "Отправь /cancel для отмены.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFSM.waiting_rss_url)
async def fsm_add_rss(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb())
        return
    parts = message.text.strip().split()
    url = parts[0]
    locale = parts[1] if len(parts) > 1 and parts[1] in ("ru", "hy", "any", "kz", "kg") else "ru"
    ok = await queries.add_source("rss", "media", locale, url)
    await state.clear()
    if ok:
        await message.answer(f"✅ RSS добавлен: {url}", reply_markup=main_menu_kb())
    else:
        await message.answer(f"⚠️ Уже существует: {url}", reply_markup=main_menu_kb())


# ── Publish channels ───────────────────────────────────────────────────────

def channels_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список каналов публикации", callback_data="ch:list")],
        [InlineKeyboardButton(text="➕ Добавить канал публикации", callback_data="ch:add")],
        [InlineKeyboardButton(text="📝 Чат черновиков", callback_data="ch:drafts_chat")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")],
    ])


@router.callback_query(F.data == "menu:channels")
async def menu_channels(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "<b>📢 Каналы публикации</b>",
        parse_mode="HTML",
        reply_markup=channels_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "ch:list")
async def ch_list(callback: CallbackQuery) -> None:
    channels = await queries.get_publish_channels_full()
    if not channels:
        await callback.answer("Нет каналов.", show_alert=True)
        return
    buttons = []
    for ch in channels:
        label = ch.get("label") or str(ch["channel_id"])
        buttons.append([InlineKeyboardButton(
            text=f"❌ [{ch['locale'].upper()}] {label}",
            callback_data=f"ch:del:{ch['channel_id']}",
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:channels")])
    await callback.message.edit_text(
        "<b>Активные каналы публикации:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ch:del:"))
async def ch_delete(callback: CallbackQuery) -> None:
    channel_id = int(callback.data.split(":")[2])
    await queries.remove_publish_channel(channel_id)
    await callback.answer("✅ Канал удалён.", show_alert=True)
    await ch_list(callback)


@router.callback_query(F.data == "ch:drafts_chat")
async def ch_drafts_chat(callback: CallbackQuery, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    current = await queries.get_setting("drafts_chat_id", "")
    current_text = f"Текущий чат: <code>{current}</code>" if current else "Чат черновиков не настроен."
    await state.set_state(AdminFSM.waiting_drafts_chat_id)
    await callback.message.edit_text(
        f"<b>📝 Чат для черновиков</b>\n\n"
        f"{current_text}\n\n"
        "Создай приватную группу в Telegram, добавь туда бота администратором.\n"
        "Затем отправь любое сообщение в группу, перешли его боту — бот получит ID.\n\n"
        "Или введи ID группы вручную (отрицательное число, например: <code>-1001234567890</code>)\n\n"
        "Отправь /cancel для отмены.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFSM.waiting_drafts_chat_id)
async def fsm_set_drafts_chat(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb())
        return
    # Accept forwarded message (extract chat id) or direct number
    if message.forward_from_chat:
        chat_id = message.forward_from_chat.id
    else:
        try:
            chat_id = int(message.text.strip())
        except ValueError:
            await message.answer("Неверный формат. Введи число, например: -1001234567890")
            return
    await queries.set_setting("drafts_chat_id", str(chat_id))
    await state.clear()
    await message.answer(
        f"✅ Чат черновиков установлен: <code>{chat_id}</code>\n\n"
        "Теперь все новые черновики будут приходить туда с кнопками одобрения.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "ch:add")
async def ch_add(callback: CallbackQuery, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    await state.set_state(AdminFSM.waiting_publish_channel_id)
    await callback.message.edit_text(
        "Введи данные канала:\n"
        "<code>channel_id locale @username</code>\n\n"
        "Пример: <code>-1001234567890 ru @GTTnews</code>\n\n"
        "ID канала: открой web.telegram.org → канал → посмотри URL (число после #)\n\n"
        "Отправь /cancel для отмены.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFSM.waiting_publish_channel_id)
async def fsm_add_channel(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb())
        return
    parts = message.text.strip().split()
    try:
        channel_id = int(parts[0])
        locale = parts[1] if len(parts) > 1 else "ru"
        label = parts[2] if len(parts) > 2 else str(channel_id)
        await queries.add_publish_channel(locale, channel_id, label)
        await state.clear()
        await message.answer(f"✅ Канал {label} [{locale}] добавлен.", reply_markup=main_menu_kb())
    except (ValueError, IndexError):
        await message.answer("Неверный формат. Пример: -1001234567890 ru @GTTnews")


# ── Prompts ────────────────────────────────────────────────────────────────

PROMPT_KEYS = {
    "gototop_context": "Контекст GoToTop (кто мы, для кого)",
    "post_style": "Стиль постов",
    "min_confidence": "Мин. уверенность (0.0–1.0)",
    "relevant_labels": "Релевантные темы (через запятую)",
}


def prompts_menu_kb() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"pr:edit:{key}")]
               for key, label in PROMPT_KEYS.items()]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "menu:prompts")
async def menu_prompts(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "<b>🤖 Настройки промптов Claude</b>\n\nВыбери что изменить:",
        parse_mode="HTML",
        reply_markup=prompts_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pr:edit:"))
async def pr_edit(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":")[2]
    current = await queries.get_setting(key, "—")
    await state.set_state(AdminFSM.waiting_prompt_value)
    await state.update_data(prompt_key=key)
    label = PROMPT_KEYS.get(key, key)
    await callback.message.edit_text(
        f"<b>✏️ {label}</b>\n\n"
        f"Текущее значение:\n<code>{current[:500]}</code>\n\n"
        "Отправь новый текст или /cancel для отмены.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFSM.waiting_prompt_value)
async def fsm_save_prompt(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb())
        return
    data = await state.get_data()
    key = data.get("prompt_key")
    await queries.set_setting(key, message.text.strip())
    await state.clear()
    await message.answer(f"✅ Настройка <b>{PROMPT_KEYS.get(key, key)}</b> обновлена.", parse_mode="HTML", reply_markup=main_menu_kb())


# ── Settings ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:settings")
async def menu_settings(callback: CallbackQuery) -> None:
    min_conf = await queries.get_setting("min_confidence", "0.45")
    labels = await queries.get_setting("relevant_labels", "")
    label_count = len(labels.split(",")) if labels else 0
    text = (
        "<b>⚙️ Фильтры и настройки</b>\n\n"
        f"🎯 Мин. уверенность Claude: <b>{min_conf}</b>\n"
        f"🏷 Релевантных тем: <b>{label_count}</b>\n\n"
        "Изменить через раздел <b>Промпты Claude</b> → фильтры."
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb())
    await callback.answer()


# ── Keywords ───────────────────────────────────────────────────────────────

def _kw_list(raw: str) -> list[str]:
    return [k.strip() for k in raw.split(",") if k.strip()]


def keywords_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 CORE ключевые слова", callback_data="kw:view:core")],
        [InlineKeyboardButton(text="📎 CONTEXT ключевые слова", callback_data="kw:view:context")],
        [InlineKeyboardButton(text="➕ Добавить CORE", callback_data="kw:add:core")],
        [InlineKeyboardButton(text="➕ Добавить CONTEXT", callback_data="kw:add:context")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")],
    ])


@router.callback_query(F.data == "menu:keywords")
async def menu_keywords(callback: CallbackQuery) -> None:
    core_raw = await queries.get_setting("filter_core_keywords", "")
    context_raw = await queries.get_setting("filter_context_keywords", "")
    core_count = len(_kw_list(core_raw))
    context_count = len(_kw_list(context_raw))
    text = (
        "<b>🔑 Управление ключевыми словами</b>\n\n"
        "<b>CORE</b> — главные слова. Достаточно 1 совпадения — текст проходит фильтр.\n"
        f"Сейчас: <b>{core_count} слов</b>\n\n"
        "<b>CONTEXT</b> — вспомогательные. Нужно 2+ совпадения (если нет ни одного CORE).\n"
        f"Сейчас: <b>{context_count} слов</b>\n\n"
        "Для государственных источников (kremlin.ru, gov.am) требуется хотя бы 1 CORE-слово."
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keywords_menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("kw:view:"))
async def kw_view(callback: CallbackQuery) -> None:
    kw_type = callback.data.split(":")[2]
    setting_key = f"filter_{kw_type}_keywords"
    raw = await queries.get_setting(setting_key, "")
    words = _kw_list(raw)
    label = "CORE" if kw_type == "core" else "CONTEXT"

    if not words:
        await callback.answer(f"Список {label} пуст.", show_alert=True)
        return

    # Each keyword becomes a ❌-button for deletion
    buttons = []
    for i, w in enumerate(words):
        buttons.append([InlineKeyboardButton(
            text=f"❌ {w}",
            callback_data=f"kw:del:{kw_type}:{i}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Добавить", callback_data=f"kw:add:{kw_type}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:keywords")])

    await callback.message.edit_text(
        f"<b>🔑 {label} ключевые слова ({len(words)}):</b>\n\n"
        + "\n".join(f"• <code>{w}</code>" for w in words),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kw:del:"))
async def kw_delete(callback: CallbackQuery) -> None:
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    _, _, kw_type, idx_str = callback.data.split(":")
    setting_key = f"filter_{kw_type}_keywords"
    raw = await queries.get_setting(setting_key, "")
    words = _kw_list(raw)
    idx = int(idx_str)
    if 0 <= idx < len(words):
        removed = words.pop(idx)
        await queries.set_setting(setting_key, ",".join(words))
        # Reset ingestion cache so new keywords take effect
        import bot.services.ingestion as ing
        ing._cached_core_kw = None
        await callback.answer(f"Удалено: {removed}", show_alert=True)
    await kw_view(callback)


@router.callback_query(F.data.startswith("kw:add:"))
async def kw_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    kw_type = callback.data.split(":")[2]
    label = "CORE" if kw_type == "core" else "CONTEXT"
    await state.set_state(AdminFSM.waiting_keyword_add)
    await state.update_data(kw_type=kw_type)
    await callback.message.edit_text(
        f"<b>➕ Добавить {label} ключевое слово</b>\n\n"
        "Введи одно слово или фразу (на русском или английском).\n"
        "Несколько слов — через запятую: <code>слово1, слово2</code>\n\n"
        "Отправь /cancel для отмены.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFSM.waiting_keyword_add)
async def kw_add_save(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb())
        return

    data = await state.get_data()
    kw_type = data.get("kw_type", "core")
    setting_key = f"filter_{kw_type}_keywords"
    label = "CORE" if kw_type == "core" else "CONTEXT"

    # Parse new words (comma-separated)
    new_words = [w.strip().lower() for w in message.text.split(",") if w.strip()]
    if not new_words:
        await message.answer("Не распознал слова. Попробуй ещё раз.")
        return

    raw = await queries.get_setting(setting_key, "")
    existing = _kw_list(raw)
    added = []
    for w in new_words:
        if w not in existing:
            existing.append(w)
            added.append(w)

    await queries.set_setting(setting_key, ",".join(existing))

    # Reset ingestion cache
    import bot.services.ingestion as ing
    ing._cached_core_kw = None

    await state.clear()
    if added:
        await message.answer(
            f"✅ Добавлено в <b>{label}</b>: {', '.join(f'<code>{w}</code>' for w in added)}\n"
            f"Всего слов: {len(existing)}",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
    else:
        await message.answer(
            f"⚠️ Все слова уже есть в списке {label}.",
            reply_markup=main_menu_kb(),
        )


# ── Strategies ─────────────────────────────────────────────────────────────

STRATEGY_CATEGORIES = {
    "sales": "💰 Продажи",
    "seo": "🔍 SEO и карточки",
    "competitor": "🕵️ Конкуренты",
    "promotion": "📣 Продвижение",
    "general": "📌 Общее",
}


def strategies_menu_kb() -> InlineKeyboardMarkup:
    buttons = []
    for cat, label in STRATEGY_CATEGORIES.items():
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"str:list:{cat}")])
    buttons.append([InlineKeyboardButton(text="📋 Все стратегии", callback_data="str:list:all")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить стратегию", callback_data="str:add")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "menu:strategies")
async def menu_strategies(callback: CallbackQuery) -> None:
    strategies = await queries.get_strategies(limit=100)
    total = len(strategies)
    await callback.message.edit_text(
        "<b>🧠 Стратегии GoToTop</b>\n\n"
        "Здесь хранятся стратегии компании — по продажам, SEO, конкурентам, продвижению.\n"
        "Claude использует их как контекст при создании каждого поста.\n"
        "Все стратегии также сохраняются в Obsidian.\n\n"
        f"Всего стратегий: <b>{total}</b>\n\n"
        "Выбери категорию:",
        parse_mode="HTML",
        reply_markup=strategies_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("str:list:"))
async def str_list(callback: CallbackQuery) -> None:
    category = callback.data.split(":")[2]
    strategies = await queries.get_strategies(
        category=None if category == "all" else category, limit=20
    )
    cat_label = STRATEGY_CATEGORIES.get(category, "Все")

    if not strategies:
        await callback.answer(f"В категории «{cat_label}» нет стратегий.", show_alert=True)
        return

    buttons = []
    for s in strategies:
        cat_icon = STRATEGY_CATEGORIES.get(s["category"], "📌").split()[0]
        buttons.append([InlineKeyboardButton(
            text=f"{cat_icon} {s['title'][:40]}",
            callback_data=f"str:view:{s['id']}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Добавить", callback_data="str:add")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:strategies")])

    await callback.message.edit_text(
        f"<b>🧠 Стратегии — {cat_label} ({len(strategies)})</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("str:view:"))
async def str_view(callback: CallbackQuery) -> None:
    strategy_id = int(callback.data.split(":")[2])
    s = await queries.get_strategy(strategy_id)
    if not s:
        await callback.answer("Стратегия не найдена.", show_alert=True)
        return
    cat_label = STRATEGY_CATEGORIES.get(s["category"], s["category"])
    date_str = str(s["created_at"])[:10]
    await callback.message.edit_text(
        f"<b>{s['title']}</b>\n"
        f"<i>{cat_label} · {date_str}</i>\n\n"
        f"{s['body']}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить", callback_data=f"str:del:{strategy_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"str:list:{s['category']}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("str:del:"))
async def str_delete(callback: CallbackQuery) -> None:
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    strategy_id = int(callback.data.split(":")[2])
    await queries.delete_strategy(strategy_id)
    await callback.answer("✅ Стратегия удалена.", show_alert=True)
    await menu_strategies(callback)


@router.callback_query(F.data == "str:add")
async def str_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if callback.from_user.id not in admin_ids:
        await callback.answer("Только для администраторов.", show_alert=True)
        return
    await state.set_state(AdminFSM.waiting_strategy_title)
    await callback.message.edit_text(
        "<b>➕ Новая стратегия</b>\n\n"
        "Выбери категорию:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"str:cat:{cat}")]
            for cat, label in STRATEGY_CATEGORIES.items()
        ] + [[InlineKeyboardButton(text="◀️ Отмена", callback_data="menu:strategies")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("str:cat:"))
async def str_choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    category = callback.data.split(":")[2]
    await state.update_data(strategy_category=category)
    await state.set_state(AdminFSM.waiting_strategy_title)
    label = STRATEGY_CATEGORIES.get(category, category)
    await callback.message.edit_text(
        f"<b>➕ Стратегия — {label}</b>\n\n"
        "Введи <b>название</b> стратегии (коротко, 3-7 слов):\n\n"
        "Отправь /cancel для отмены.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFSM.waiting_strategy_title)
async def str_get_title(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb())
        return
    await state.update_data(strategy_title=message.text.strip())
    await state.set_state(AdminFSM.waiting_strategy_body)
    await message.answer(
        f"📌 Название: <b>{message.text.strip()}</b>\n\n"
        "Теперь введи текст стратегии.\n"
        "Опиши подход, тактику, конкретные шаги — всё что нужно знать.\n\n"
        "Отправь /cancel для отмены.",
        parse_mode="HTML",
    )


@router.message(AdminFSM.waiting_strategy_body)
async def str_get_body(message: Message, state: FSMContext) -> None:
    admin_ids = await queries.get_admin_ids()
    if message.from_user.id not in admin_ids:
        await state.clear()
        return
    if message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_kb())
        return

    data = await state.get_data()
    title = data.get("strategy_title", "Без названия")
    category = data.get("strategy_category", "general")
    body = message.text.strip()

    strategy_id = await queries.save_strategy(title, body, category, message.from_user.id)
    await state.clear()

    # Save to Obsidian
    try:
        from bot.services.obsidian import save_strategy_to_obsidian
        await save_strategy_to_obsidian(
            title=title, body=body, category=category,
            strategy_id=strategy_id,
        )
    except Exception as e:
        print(f"[obsidian] Strategy save failed: {e}")

    cat_label = STRATEGY_CATEGORIES.get(category, category)
    await message.answer(
        f"✅ Стратегия сохранена!\n\n"
        f"<b>{title}</b> [{cat_label}]\n\n"
        "Claude будет использовать её при создании следующих постов.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


# ── Back to main ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "<b>⚙️ Панель управления GoToTop Analytics</b>\n\nВыбери раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()
