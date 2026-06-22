"""Settings router: the language picker (Settings button and /language command)."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..filters import MainMenuButton
from ..i18n import (
    _,
    LANGUAGE_NAMES,
    SUPPORTED_LANGUAGES,
    set_current_language,
    set_user_language,
)
from ..keyboards import get_language_keyboard, get_main_menu_keyboard
from ..utils import handle_telegram_errors, safe_edit_message

logger = logging.getLogger(__name__)

# Create the router for settings / language
settings_router = Router(name="settings")


def _settings_text(language: str) -> str:
    """Build the settings screen body for a language.

    Args:
        language: Language code to render the screen and highlight as current.

    Returns:
        str: The HTML settings text (title, current language, choose-a-language
            prompt).
    """
    return (
        _("settings.title")
        + "\n\n"
        + _("settings.language_current", current=LANGUAGE_NAMES[language])
        + "\n\n"
        + _("settings.choose_language")
    )


@settings_router.message(MainMenuButton("menu.settings"))
async def cmd_settings(message: Message, language: str) -> None:
    """Handle the Settings reply-keyboard button: show the language picker.

    Args:
        message: Incoming reply-keyboard tap message.
        language: The user's active language (injected by LanguageMiddleware).

    Returns:
        None.
    """
    await message.answer(_settings_text(language), reply_markup=get_language_keyboard(language))


@settings_router.message(Command("language"))
async def cmd_language(message: Message, language: str) -> None:
    """Handle the /language command: show the language picker.

    Args:
        message: Incoming /language command message.
        language: The user's active language (injected by LanguageMiddleware).

    Returns:
        None.
    """
    await message.answer(_settings_text(language), reply_markup=get_language_keyboard(language))


@settings_router.callback_query(F.data.startswith("set_lang_"))
@handle_telegram_errors
async def callback_set_language(callback: CallbackQuery) -> None:
    """Persist the chosen language and refresh the UI in that language.

    Stores the choice, activates it for the rest of this update, edits the
    settings message in the new language, and re-sends the main-menu reply
    keyboard (reply keyboards cannot be edited in place, so their labels are only
    refreshed by sending a new one).

    Args:
        callback: Callback query whose data is ``set_lang_<code>``.

    Returns:
        None.
    """
    new_language = callback.data.removeprefix("set_lang_")
    if new_language not in SUPPORTED_LANGUAGES:
        await callback.answer(_("common.unknown_operation"))
        logger.warning("Unknown language in callback_data: %r", callback.data)
        return

    # Persist off the event loop (the store does a small atomic file write), then
    # activate for the rest of this update so every _() below (toast, settings
    # message, keyboards) renders in the newly chosen language.
    persisted = await asyncio.to_thread(set_user_language, callback.from_user.id, new_language)
    # Apply for this running process regardless, so the rest of this update (and the
    # session) renders in the chosen language.
    set_current_language(new_language)

    if persisted:
        await callback.answer(_("settings.language_changed"))
    else:
        # The disk write failed (logged by the store): the change is in-memory only
        # and will reset on restart. Tell the user instead of claiming success.
        logger.warning(
            "Language preference for user %s applied in-memory only (not persisted)",
            callback.from_user.id,
        )
        await callback.answer(_("settings.language_not_saved"), show_alert=True)

    await safe_edit_message(callback, _settings_text(new_language), get_language_keyboard(new_language))

    # Re-send the main menu so its (reply-keyboard) labels switch language too.
    if callback.message is not None:
        await callback.message.answer(
            _("settings.menu_updated"), reply_markup=get_main_menu_keyboard()
        )
