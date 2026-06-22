"""Settings router for the hub screen and its language section.

The Settings reply button lands on a small inline "hub" menu (one button per
settings section), and tapping a section edits that hub into the selected
section. Today the only section is the language picker, but the hub is the
extension point for future settings: a new section needs one more button in
``get_settings_menu_keyboard`` plus matching open/back callbacks here. The
``/language`` command is a shortcut straight to the language section.
"""

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
from ..keyboards import (
    SETTINGS_SECTIONS,
    get_language_keyboard,
    get_main_menu_keyboard,
    get_settings_menu_keyboard,
)
from ..utils import (
    handle_telegram_errors,
    reset_screen_from_callback,
    safe_edit_message,
    show_screen,
)
from ..utils.rich import blocks, stack

logger = logging.getLogger(__name__)

# Router for the settings hub, language section, and language-selection callbacks.
settings_router = Router(name="settings")


def _menu_text() -> str:
    """Build the settings hub screen body, listing each section with a description.

    Returns:
        str: The HTML hub text — title, the choose-a-section prompt, then one
            ``label — description`` line per section in SETTINGS_SECTIONS.
    """
    sections = stack(
        *(f"{_(label_key)} — {_(desc_key)}" for label_key, desc_key, _cb in SETTINGS_SECTIONS)
    )
    return blocks(_("settings.title"), _("settings.choose_section"), sections)


def _language_text(language: str) -> str:
    """Build the language-section screen body for a language.

    Args:
        language: Language code whose proper name is shown as the current one.

    Returns:
        str: The HTML language-section text with a settings/language breadcrumb
            followed by the current language.
    """
    breadcrumb = f"{_('settings.title')} › {_('settings.section_language')}"
    return blocks(breadcrumb, _("settings.language_current", current=LANGUAGE_NAMES[language]))


@settings_router.message(MainMenuButton("menu.settings"))
async def cmd_settings(message: Message) -> None:
    """Handle the Settings reply-keyboard button: show the settings hub.

    Args:
        message: Incoming reply-keyboard tap message.

    Returns:
        None.
    """
    # Sent as the single live section screen (deletes this chat's previous one).
    await show_screen(message, _menu_text(), get_settings_menu_keyboard())


@settings_router.message(Command("language"))
async def cmd_language(message: Message, language: str) -> None:
    """Handle the /language command: jump straight to the language section.

    Args:
        message: Incoming /language command message.
        language: The user's active language (injected by LanguageMiddleware).

    Returns:
        None.
    """
    await show_screen(message, _language_text(language), get_language_keyboard(language))


@settings_router.callback_query(F.data == "settings_lang")
@handle_telegram_errors
async def callback_open_language(callback: CallbackQuery, language: str) -> None:
    """Open the language section from the settings hub (edits the hub in place).

    Args:
        callback: Callback query from the hub's language button.
        language: The user's active language (injected by LanguageMiddleware).

    Returns:
        None.
    """
    await safe_edit_message(callback, _language_text(language), get_language_keyboard(language))
    await callback.answer()


@settings_router.callback_query(F.data == "settings_back")
@handle_telegram_errors
async def callback_settings_back(callback: CallbackQuery) -> None:
    """Return from a settings section to the settings hub (edits in place).

    Args:
        callback: Callback query from a section's Back button.

    Returns:
        None.
    """
    await safe_edit_message(callback, _menu_text(), get_settings_menu_keyboard())
    await callback.answer()


@settings_router.callback_query(F.data.startswith("set_lang_"))
@handle_telegram_errors
async def callback_set_language(callback: CallbackQuery) -> None:
    """Persist the chosen language and drop to one clean main-menu screen in it.

    Stores the choice, activates it for the rest of this update, then replaces the
    language picker with a single persistent main-menu screen rendered in the new
    language. Reply-keyboard labels cannot be edited in place, so a language switch
    must re-send the main menu; routing that through ``reset_screen_from_callback``
    removes the picker (even when the single-screen tracker is stale after a
    restart) and sends the menu UNtracked, so its reply keyboard survives later
    navigation — leaving the chat with one clean screen instead of a leftover
    (still interactive) picker plus a separate notice.

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

    # Reply-keyboard labels cannot be edited in place, so a language switch must
    # re-send the main menu. reset_screen_from_callback removes the picker and sends
    # the menu as a PERSISTENT (untracked) screen, so its reply keyboard is not lost
    # when the user later navigates to another section.
    await reset_screen_from_callback(
        callback, _("settings.menu_updated"), get_main_menu_keyboard()
    )
