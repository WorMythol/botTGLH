"""
Telegram-бот для регистрации и авторизации пользователей.
Использует aiogram 3.x и Database из database.py.
Требуется Python 3.11+
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional, Union

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)

from database import Database

# ──────────────────────────────────────────────
# Настройка логирования
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Конфигурация (через env-переменные)
# ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://user:password@localhost:5432/mydb",
)
ADMIN_IDS: list = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

# ──────────────────────────────────────────────
# Валидация
# ──────────────────────────────────────────────
LOGIN_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
PASSWORD_MIN_LEN = 6


def validate_login(login: str) -> Optional[str]:
    """Возвращает сообщение об ошибке или None если всё ок."""
    if not LOGIN_RE.match(login):
        return (
            "❌ Логин должен содержать от 3 до 32 символов: "
            "латинские буквы, цифры и знак подчёркивания."
        )
    return None


def validate_password(password: str) -> Optional[str]:
    if len(password) < PASSWORD_MIN_LEN:
        return f"❌ Пароль должен быть не менее {PASSWORD_MIN_LEN} символов."
    return None


# ──────────────────────────────────────────────
# FSM — состояния
# ──────────────────────────────────────────────
class RegisterStates(StatesGroup):
    waiting_login = State()
    waiting_password = State()
    waiting_password_confirm = State()


class LoginStates(StatesGroup):
    waiting_login = State()
    waiting_password = State()


class ChangePasswordStates(StatesGroup):
    waiting_old_password = State()
    waiting_new_password = State()
    waiting_new_password_confirm = State()


class ChangeLoginStates(StatesGroup):
    waiting_new_login = State()
    waiting_password_confirm = State()


# ──────────────────────────────────────────────
# Клавиатуры
# ──────────────────────────────────────────────
def main_menu_guest() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📝 Зарегистрироваться", callback_data="register"),
            InlineKeyboardButton(text="🔑 Войти", callback_data="login"),
        ]
    ])


def main_menu_user() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile")],
        [
            InlineKeyboardButton(text="🔄 Сменить логин", callback_data="change_login"),
            InlineKeyboardButton(text="🔒 Сменить пароль", callback_data="change_password"),
        ],
        [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data="delete_account")],
    ])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])


def confirm_delete_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete"),
            InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel"),
        ]
    ])


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="🚫 Заблокированные", callback_data="admin_banned")],
    ])


# ──────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────
def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS


async def safe_delete(message: Message) -> None:
    """Пытается удалить сообщение (пароли не должны висеть в чате)."""
    try:
        await message.delete()
    except Exception:
        pass


def format_profile(user: dict) -> str:
    reg = str(user["registered_at"])[:19].replace("T", " ")
    status_icon = "✅" if user["status"] == "active" else "🚫"
    return (
        f"👤 <b>Профиль</b>\n\n"
        f"• Логин: <code>{user['login']}</code>\n"
        f"• Telegram ID: <code>{user['telegram_id']}</code>\n"
        f"• Статус: {status_icon} {user['status']}\n"
        f"• Зарегистрирован: {reg}"
    )


# ──────────────────────────────────────────────
# Роутер
# ──────────────────────────────────────────────
router = Router()
db: Database  # инициализируется в main()


# ── /start ───────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = db.get_user_by_telegram_id(message.from_user.id)
    if user:
        await message.answer(
            f"👋 С возвращением, <b>{user['login']}</b>!\n\nВыберите действие:",
            reply_markup=main_menu_user(),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "👋 Добро пожаловать!\n\n"
            "Вы ещё не зарегистрированы. Выберите действие:",
            reply_markup=main_menu_guest(),
        )


# ── /help ────────────────────────────────────
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "ℹ️ <b>Доступные команды:</b>\n\n"
        "/start — главное меню\n"
        "/profile — мой профиль\n"
        "/cancel — отмена текущего действия\n"
    )
    if is_admin(message.from_user.id):
        text += "\n🔧 <b>Администратор:</b>\n/admin — панель администратора\n"
    await message.answer(text, parse_mode="HTML")


# ── /cancel ──────────────────────────────────
@router.message(Command("cancel"))
@router.callback_query(F.data == "cancel")
async def cmd_cancel(event: Union[Message, CallbackQuery], state: FSMContext) -> None:
    await state.clear()
    msg = event if isinstance(event, Message) else event.message    user = db.get_user_by_telegram_id(
        event.from_user.id if isinstance(event, Message) else event.from_user.id
    )
    text = "🏠 Главное меню:"
    kb = main_menu_user() if user else main_menu_guest()
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)


# ══════════════════════════════════════════════
# РЕГИСТРАЦИЯ
# ══════════════════════════════════════════════
@router.callback_query(F.data == "register")
async def cb_register(callback: CallbackQuery, state: FSMContext) -> None:
    if db.get_user_by_telegram_id(callback.from_user.id):
        await callback.answer("Вы уже зарегистрированы!", show_alert=True)
        return
    await callback.answer()
    await state.set_state(RegisterStates.waiting_login)
    await callback.message.edit_text(
        "📝 <b>Регистрация</b>\n\nШаг 1/3 — Придумайте логин:\n"
        "<i>(3–32 символа: буквы, цифры, _)</i>",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.message(RegisterStates.waiting_login)
async def reg_got_login(message: Message, state: FSMContext) -> None:
    login = message.text.strip()
    err = validate_login(login)
    if err:
        await message.answer(err + "\n\nПопробуйте ещё раз:", reply_markup=cancel_kb())
        return
    if db.login_exists(login):
        await message.answer(
            "❌ Этот логин уже занят. Выберите другой:",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(login=login)
    await state.set_state(RegisterStates.waiting_password)
    await message.answer(
        f"✅ Логин <code>{login}</code> свободен!\n\n"
        f"Шаг 2/3 — Придумайте пароль:\n"
        f"<i>(минимум {PASSWORD_MIN_LEN} символов)</i>",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.message(RegisterStates.waiting_password)
async def reg_got_password(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    password = message.text.strip()
    err = validate_password(password)
    if err:
        await message.answer(err + "\n\nПопробуйте ещё раз:", reply_markup=cancel_kb())
        return

    await state.update_data(password=password)
    await state.set_state(RegisterStates.waiting_password_confirm)
    await message.answer(
        "Шаг 3/3 — Повторите пароль для подтверждения:",
        reply_markup=cancel_kb(),
    )


@router.message(RegisterStates.waiting_password_confirm)
async def reg_got_confirm(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    data = await state.get_data()
    if message.text.strip() != data["password"]:
        await message.answer(
            "❌ Пароли не совпадают. Введите пароль заново:",
            reply_markup=cancel_kb(),
        )
        await state.set_state(RegisterStates.waiting_password)
        return

    ok = db.register_user(message.from_user.id, data["login"], data["password"])
    await state.clear()

    if ok:
        await message.answer(
            f"🎉 Регистрация успешна!\n\nВаш логин: <code>{data['login']}</code>",
            reply_markup=main_menu_user(),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "❌ Ошибка регистрации. Возможно, логин или аккаунт уже существует.",
            reply_markup=main_menu_guest(),
        )


# ══════════════════════════════════════════════
# АВТОРИЗАЦИЯ (проверка пароля)
# ══════════════════════════════════════════════
@router.callback_query(F.data == "login")
async def cb_login(callback: CallbackQuery, state: FSMContext) -> None:
    if db.get_user_by_telegram_id(callback.from_user.id):
        await callback.answer("Вы уже вошли в систему!", show_alert=True)
        return
    await callback.answer()
    await state.set_state(LoginStates.waiting_login)
    await callback.message.edit_text(
        "🔑 <b>Вход в аккаунт</b>\n\nВведите ваш логин:",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.message(LoginStates.waiting_login)
async def login_got_login(message: Message, state: FSMContext) -> None:
    login = message.text.strip()
    user = db.get_user_by_login(login)
    if not user:
        await message.answer(
            "❌ Пользователь с таким логином не найден.",
            reply_markup=cancel_kb(),
        )
        return
    if user["status"] == "banned":
        await message.answer(
            "🚫 Ваш аккаунт заблокирован. Обратитесь к администратору.",
            reply_markup=cancel_kb(),
        )
        await state.clear()
        return
    if user["telegram_id"] != message.from_user.id:
        await message.answer(
            "❌ Этот аккаунт привязан к другому Telegram.",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(login=login, password_hash=user["password_hash"])
    await state.set_state(LoginStates.waiting_password)
    await message.answer("Введите пароль:", reply_markup=cancel_kb())


@router.message(LoginStates.waiting_password)
async def login_got_password(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    data = await state.get_data()
    if not db.verify_password(message.text.strip(), data["password_hash"]):
        await message.answer("❌ Неверный пароль. Попробуйте ещё раз:", reply_markup=cancel_kb())
        return

    await state.clear()
    await message.answer(
        f"✅ Добро пожаловать, <b>{data['login']}</b>!",
        reply_markup=main_menu_user(),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════
# ПРОФИЛЬ
# ══════════════════════════════════════════════
@router.message(Command("profile"))
@router.callback_query(F.data == "profile")
async def show_profile(event: Union[Message, CallbackQuery]) -> None:
    tg_id = event.from_user.id
    user = db.get_user_by_telegram_id(tg_id)
    if not user:
        text = "❌ Вы не зарегистрированы."
        kb = main_menu_guest()
        if isinstance(event, CallbackQuery):
            await event.answer()
            await event.message.edit_text(text, reply_markup=kb)
        else:
            await event.answer(text, reply_markup=kb)
        return

    text = format_profile(user)
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=main_menu_user(), parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=main_menu_user(), parse_mode="HTML")


# ══════════════════════════════════════════════
# СМЕНА ПАРОЛЯ
# ══════════════════════════════════════════════
@router.callback_query(F.data == "change_password")
async def cb_change_password(callback: CallbackQuery, state: FSMContext) -> None:
    if not db.get_user_by_telegram_id(callback.from_user.id):
        await callback.answer("Сначала зарегистрируйтесь!", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ChangePasswordStates.waiting_old_password)
    await callback.message.edit_text(
        "🔒 <b>Смена пароля</b>\n\nВведите текущий пароль:",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.message(ChangePasswordStates.waiting_old_password)
async def chpw_old(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not db.verify_password(message.text.strip(), user["password_hash"]):
        await message.answer("❌ Неверный текущий пароль:", reply_markup=cancel_kb())
        return
    await state.set_state(ChangePasswordStates.waiting_new_password)
    await message.answer(
        f"✅ Верно! Введите новый пароль (минимум {PASSWORD_MIN_LEN} символов):",
        reply_markup=cancel_kb(),
    )


@router.message(ChangePasswordStates.waiting_new_password)
async def chpw_new(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    err = validate_password(message.text.strip())
    if err:
        await message.answer(err, reply_markup=cancel_kb())
        return
    await state.update_data(new_password=message.text.strip())
    await state.set_state(ChangePasswordStates.waiting_new_password_confirm)
    await message.answer("Повторите новый пароль:", reply_markup=cancel_kb())


@router.message(ChangePasswordStates.waiting_new_password_confirm)
async def chpw_confirm(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    data = await state.get_data()
    if message.text.strip() != data["new_password"]:
        await message.answer("❌ Пароли не совпадают. Введите новый пароль заново:", reply_markup=cancel_kb())
        await state.set_state(ChangePasswordStates.waiting_new_password)
        return

    ok = db.update_password(message.from_user.id, data["new_password"])
    await state.clear()
    if ok:
        await message.answer("✅ Пароль успешно изменён!", reply_markup=main_menu_user())
    else:
        await message.answer("❌ Ошибка при смене пароля.", reply_markup=main_menu_user())


# ══════════════════════════════════════════════
# СМЕНА ЛОГИНА
# ══════════════════════════════════════════════
@router.callback_query(F.data == "change_login")
async def cb_change_login(callback: CallbackQuery, state: FSMContext) -> None:
    if not db.get_user_by_telegram_id(callback.from_user.id):
        await callback.answer("Сначала зарегистрируйтесь!", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ChangeLoginStates.waiting_new_login)
    await callback.message.edit_text(
        "🔄 <b>Смена логина</b>\n\nВведите новый логин:",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.message(ChangeLoginStates.waiting_new_login)
async def chl_new_login(message: Message, state: FSMContext) -> None:
    login = message.text.strip()
    err = validate_login(login)
    if err:
        await message.answer(err, reply_markup=cancel_kb())
        return
    if db.login_exists(login):
        await message.answer("❌ Логин уже занят. Выберите другой:", reply_markup=cancel_kb())
        return
    await state.update_data(new_login=login)
    await state.set_state(ChangeLoginStates.waiting_password_confirm)
    await message.answer(
        f"Логин <code>{login}</code> свободен!\n\nПодтвердите действие — введите ваш пароль:",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.message(ChangeLoginStates.waiting_password_confirm)
async def chl_confirm(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    user = db.get_user_by_telegram_id(message.from_user.id)
    if not db.verify_password(message.text.strip(), user["password_hash"]):
        await message.answer("❌ Неверный пароль:", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    ok = db.update_login(message.from_user.id, data["new_login"])
    await state.clear()
    if ok:
        await message.answer(
            f"✅ Логин изменён на <code>{data['new_login']}</code>!",
            reply_markup=main_menu_user(),
            parse_mode="HTML",
        )
    else:
        await message.answer("❌ Ошибка при смене логина.", reply_markup=main_menu_user())


# ══════════════════════════════════════════════
# УДАЛЕНИЕ АККАУНТА
# ══════════════════════════════════════════════
@router.callback_query(F.data == "delete_account")
async def cb_delete_account(callback: CallbackQuery) -> None:
    if not db.get_user_by_telegram_id(callback.from_user.id):
        await callback.answer("Аккаунт не найден!", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "⚠️ <b>Удаление аккаунта</b>\n\nВы уверены? Это действие нельзя отменить!",
        reply_markup=confirm_delete_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "confirm_delete")
async def cb_confirm_delete(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    ok = db.delete_user(callback.from_user.id)
    await callback.answer()
    if ok:
        await callback.message.edit_text(
            "🗑 Ваш аккаунт удалён. До свидания!\n\nЕсли захотите вернуться — /start",
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка при удалении аккаунта.",
            reply_markup=main_menu_user(),
        )


# ══════════════════════════════════════════════
# АДМИН-ПАНЕЛЬ
# ══════════════════════════════════════════════
@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    await message.answer("🔧 <b>Панель администратора</b>", reply_markup=admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    stats = db.get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"• Всего пользователей: <b>{stats['total']}</b>\n"
        f"• Активных: <b>{stats['active']}</b>\n"
        f"• Заблокированных: <b>{stats['banned']}</b>\n"
        f"• Последняя регистрация: {stats['last_registration'] or 'нет'}"
    )
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    users = db.get_all_users()
    if not users:
        await callback.answer("Пользователей нет.", show_alert=True)
        return

    lines = ["👥 <b>Все пользователи:</b>\n"]
    for u in users[:50]:  # ограничение на 50
        icon = "✅" if u["status"] == "active" else "🚫"
        reg = str(u["registered_at"])[:10]
        lines.append(f"{icon} <code>{u['login']}</code> (tg:{u['telegram_id']}) — {reg}")

    await callback.answer()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_banned")
async def cb_admin_banned(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    users = db.get_banned_users()
    if not users:
        await callback.answer("Нет заблокированных пользователей.", show_alert=True)
        return

    lines = ["🚫 <b>Заблокированные:</b>\n"]
    for u in users:
        lines.append(f"• <code>{u['login']}</code> (tg:{u['telegram_id']})")

    await callback.answer()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


# ── /ban и /unban ─────────────────────────────
@router.message(Command("ban"))
async def cmd_ban(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /ban <логин>")
        return
    login = args[1].strip()
    ok = db.set_user_status(login, "banned", banned_by=message.from_user.id)
    await message.answer(f"✅ Пользователь <code>{login}</code> заблокирован." if ok
                         else f"❌ Пользователь <code>{login}</code> не найден.",
                         parse_mode="HTML")


@router.message(Command("unban"))
async def cmd_unban(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /unban <логин>")
        return
    login = args[1].strip()
    ok = db.set_user_status(login, "active")
    await message.answer(f"✅ Пользователь <code>{login}</code> разблокирован." if ok
                         else f"❌ Пользователь <code>{login}</code> не найден.",
                         parse_mode="HTML")


@router.message(Command("deluser"))
async def cmd_deluser(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /deluser <логин>")
        return
    login = args[1].strip()
    ok = db.delete_user_by_login(login, deleted_by=f"admin:{message.from_user.id}")
    await message.answer(f"✅ Пользователь <code>{login}</code> удалён." if ok
                         else f"❌ Пользователь <code>{login}</code> не найден.",
                         parse_mode="HTML")


# ── fallback для неизвестных сообщений ────────
@router.message(StateFilter(None))
async def fallback(message: Message) -> None:
    user = db.get_user_by_telegram_id(message.from_user.id)
    await message.answer(
        "Используйте кнопки меню или команду /start",
        reply_markup=main_menu_user() if user else main_menu_guest(),
    )


# ══════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════
async def set_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start",   description="Главное меню"),
        BotCommand(command="profile", description="Мой профиль"),
        BotCommand(command="help",    description="Помощь"),
        BotCommand(command="cancel",  description="Отмена"),
    ]
    await bot.set_my_commands(commands)


async def main() -> None:
    global db

    # Инициализация БД
    db = Database(DATABASE_URL)
    db.connect()
    db.init_db()

    # Инициализация бота
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await set_commands(bot)

    logger.info("Бот запущен. Нажмите Ctrl+C для остановки.")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
