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
    waiting_prompt_key = State()
    waiting_prompt_value = State()


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
            InlineKeyboardButton(text="⚙️ Фильтры и настройки", callback_data="menu:settings"),
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
        [InlineKeyboardButton(text="📋 Список каналов", callback_data="ch:list")],
        [InlineKeyboardButton(text="➕ Добавить канал", callback_data="ch:add")],
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


@router.callback_query(F.data == "ch:add")
async def ch_add(callback: CallbackQuery, state: FSMContext) -> None:
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


# ── Back to main ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "<b>⚙️ Панель управления GoToTop Analytics</b>\n\nВыбери раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()
