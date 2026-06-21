"""Router for the /start command."""

import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..keyboards.reply import get_main_menu_keyboard

logger = logging.getLogger(__name__)

# Create the router for the /start command
start_router = Router(name="start")


@start_router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """
    Handle the /start command.

    Sends a welcome message and displays the main menu.

    Args:
        message: Incoming message carrying the /start command.
    """
    welcome_text = (
        "<b>🎉 Добро пожаловать в систему мониторинга серверов!</b>\n\n"
        "Этот бот поможет вам отслеживать состояние ваших серверов "
        "и управлять ими через Telegram.\n\n"
        "<b>Доступные функции:</b>\n"
        "📊 <b>Мониторинг</b> - просмотр статуса всех серверов\n"
        "🖥️ <b>Серверы</b> - управление серверами (добавление, удаление)\n"
        "💰 <b>Баланс</b> - информация о балансе провайдера\n"
        "⚙️ <b>Настройки</b> - настройка параметров мониторинга\n\n"
        "Используйте кнопки меню ниже для навигации 👇"
    )

    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard())
