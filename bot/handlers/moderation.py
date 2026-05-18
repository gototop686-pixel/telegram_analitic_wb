from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from bot.db import queries
from bot.services.publisher import publish_draft

router = Router()


def draft_keyboard(draft_id: int, locale: str) -> InlineKeyboardMarkup:
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
    _, draft_id_str, locale = callback.data.split(":")
    draft_id = int(draft_id_str)

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
        callback.message.text + f"\n\n✅ Опубликовано в {published_count} каналах. Модератор: @{callback.from_user.username}",
        reply_markup=None,
    )
    await callback.answer("Опубликовано!")


@router.callback_query(F.data.startswith("reject:"))
async def handle_reject(callback: CallbackQuery) -> None:
    _, draft_id_str = callback.data.split(":")
    draft_id = int(draft_id_str)

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
