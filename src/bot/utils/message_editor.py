"""Utilities for safely editing Telegram messages (rich-message path)."""

import logging

from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

from .rich import edit_rich

logger = logging.getLogger(__name__)


async def safe_edit_message(
    callback: CallbackQuery,
    new_text: str,
    new_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """
    Edit the callback's message to a new rich body, guarding harmless errors.

    The screen is replaced in place via the rich-message edit path
    (:func:`~src.bot.utils.rich.edit_rich`). Unlike the classic text path there is
    no cheap pre-edit content comparison: a received rich message stores its
    content in ``message.rich_message`` (parsed ``RichBlock`` structures), not in
    ``message.html_text``, so the new HTML cannot be compared against it
    reliably. The edit is therefore always attempted and Telegram's own "message
    is not modified" response is treated as a no-op (returns ``False``), which
    yields the same result the old pre-check did — just one API round-trip later.

    Args:
        callback: The CallbackQuery whose message is edited.
        new_text: The new rich HTML body (newlines become ``<br>``).
        new_markup: The new inline keyboard (optional).

    Returns:
        bool: True if the message was edited; False if it was skipped (the
            message is inaccessible, unchanged, or no longer exists).

    Raises:
        TelegramBadRequest: Re-raised for errors other than "message is not
            modified" / "message to edit not found".
    """
    # Make sure the message is accessible (a too-old message arrives as an
    # InaccessibleMessage, which cannot be edited).
    if not callback.message or isinstance(callback.message, InaccessibleMessage):
        logger.warning(f"Cannot edit inaccessible message (callback: {callback.data})")
        return False

    try:
        await edit_rich(callback.message, new_text, new_markup)
        return True

    except TelegramBadRequest as e:
        error_msg = str(e).lower()

        # Identical content/markup: Telegram rejects the edit — treat as a no-op.
        if "message is not modified" in error_msg:
            return False

        if "message to edit not found" in error_msg:
            logger.warning(f"Message to edit not found (callback: {callback.data})")
            return False

        # Other TelegramBadRequest errors (e.g. unsupported rich markup) - propagate.
        logger.error(f"Failed to edit message (callback: {callback.data}): {e}", exc_info=True)
        raise
