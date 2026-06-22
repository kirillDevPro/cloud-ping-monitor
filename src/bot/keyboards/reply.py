"""Reply keyboards for the Telegram bot."""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from ..i18n import _


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Build the main menu reply keyboard in the active language.

    Button labels are resolved from the i18n catalog using the language set by
    :class:`~src.bot.middlewares.language.LanguageMiddleware`, so the same builder
    renders the menu in whatever language the current user has chosen. A reply
    keyboard cannot be edited in place, so callers re-send it after a language
    change to refresh the labels.

    Layout:
    ┌────────────────┬────────────────┐
    │ Monitoring     │ Servers        │
    ├────────────────┼────────────────┤
    │ Balance        │ Settings       │
    └────────────────┴────────────────┘

    Returns:
        ReplyKeyboardMarkup: The main menu keyboard.
    """
    keyboard = [
        [KeyboardButton(text=_("menu.monitoring")), KeyboardButton(text=_("menu.servers"))],
        [KeyboardButton(text=_("menu.balance")), KeyboardButton(text=_("menu.settings"))],
    ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder=_("menu.placeholder"),
    )
