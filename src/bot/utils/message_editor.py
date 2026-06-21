"""Utilities for safely editing Telegram messages."""

import logging
from typing import Any

from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


def _keyboards_equal(
    old_markup: InlineKeyboardMarkup | None,
    new_markup: InlineKeyboardMarkup | None,
) -> bool:
    """
    Compare two inline keyboards by structure.

    Uses model_dump() for a reliable comparison instead of repr(),
    which may contain internal object IDs.

    Args:
        old_markup: The old keyboard
        new_markup: The new keyboard

    Returns:
        bool: True if the keyboards are equivalent
    """
    # Both None - equal
    if old_markup is None and new_markup is None:
        return True

    # One is None and the other is not - not equal
    if old_markup is None or new_markup is None:
        return False

    # Compare via model_dump for reliability
    try:
        old_data: dict[str, Any] = old_markup.model_dump()
        new_data: dict[str, Any] = new_markup.model_dump()
        return old_data == new_data
    except Exception as e:
        # Fall back to comparing inline_keyboard directly
        logger.debug(f"Failed to compare markups via model_dump: {e}")
        try:
            return old_markup.inline_keyboard == new_markup.inline_keyboard
        except Exception as fallback_error:
            # As a last resort treat them as different (an edit will be attempted)
            logger.debug(f"Fallback keyboard comparison also failed: {fallback_error}")
            return False


async def safe_edit_message(
    callback: CallbackQuery,
    new_text: str,
    new_markup: InlineKeyboardMarkup | None = None,
    *,
    force: bool = False,
) -> bool:
    """
    Safely edit a message with error protection.

    Checks whether the text or keyboard changed before editing in order to
    avoid the "message is not modified" error from the Telegram API.

    Args:
        callback: The CallbackQuery object from the handler
        new_text: The new message text
        new_markup: The new inline keyboard (optional)
        force: Force the edit without the change check (default False)

    Returns:
        bool: True if the message was successfully edited, False otherwise

    Raises:
        TelegramBadRequest: If an error occurs that is unrelated to an unmodified message

    Example:
        >>> await safe_edit_message(
        ...     callback,
        ...     "New text",
        ...     get_keyboard()
        ... )
        True
    """
    # Make sure the message is accessible
    if not callback.message or isinstance(callback.message, InaccessibleMessage):
        logger.warning(f"Cannot edit inaccessible message (callback: {callback.data})")
        return False

    # Quick content-change check (unless the edit is forced)
    if not force:
        # Compare against html_text (the HTML aiogram reconstructs from entities),
        # not .text (which is already parsed with tags stripped); otherwise the raw
        # HTML in new_text never equals the stripped old text and the skip is dead.
        # html_text returns "" (does not raise) when the message has no text.
        old_text = callback.message.html_text or ""
        old_markup = callback.message.reply_markup

        # Check whether the text changed
        text_changed = old_text != new_text

        # Check whether the keyboard changed
        # Use _keyboards_equal() for a reliable structural comparison
        markup_changed = not _keyboards_equal(old_markup, new_markup)

        if not text_changed and not markup_changed:
            return False

    # Attempt to edit the message
    try:
        await callback.message.edit_text(new_text, reply_markup=new_markup)
        return True

    except TelegramBadRequest as e:
        error_msg = str(e).lower()

        # Handle known, safe-to-ignore errors
        if "message is not modified" in error_msg:
            return False

        if "message to edit not found" in error_msg:
            logger.warning(f"Message to edit not found (callback: {callback.data})")
            return False

        # Other TelegramBadRequest errors - propagate upward
        logger.error(f"Failed to edit message (callback: {callback.data}): {e}", exc_info=True)
        raise
