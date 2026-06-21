"""Reply keyboards for the Telegram bot."""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Build the main menu reply keyboard.

    Layout:
    ┌────────────────┬────────────────┐
    │ Monitoring     │ Servers        │
    ├────────────────┴────────────────┤
    │ Balance                         │
    └─────────────────────────────────┘

    Returns:
        ReplyKeyboardMarkup: The main menu keyboard.
    """
    keyboard = [
        [KeyboardButton(text="📊 Мониторинг"), KeyboardButton(text="🖥️ Серверы")],
        [KeyboardButton(text="💰 Баланс")],
    ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие...",
    )
