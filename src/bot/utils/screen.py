"""Single-screen UI helpers for reply-keyboard and callback navigation.

Reply-keyboard taps arrive as new user messages, so each section handler would
otherwise send a brand-new bot message and leave the previous one behind, piling
up duplicate screens in the chat. ``show_screen`` keeps at most one live
"section screen" per chat: it sends the new screen first, then best-effort
deletes the previously tracked bot screen.

Intentional scope (decided with the user):
- The user's own tap messages are left in place (only the bot's previous screen
  is removed).
- The ``/start`` welcome is never tracked here, so it persists across navigation.

Inline-keyboard navigation inside a screen is unaffected: those handlers edit the
message in place (see ``safe_edit_message``), which preserves the tracked
``message_id``, so a later reply-button tap still deletes the right message.

``reset_screen_from_callback`` covers callback flows that must drop back to a
PERSISTENT screen carrying the reply keyboard (e.g. the main menu after a language
switch). It sends that screen UNtracked — Telegram drops a reply keyboard if its
carrier message is deleted, so the anchor must survive later navigation, like the
``/start`` welcome — then clears tracking and removes both the previously tracked
screen and the callback's source picker (so a pre-restart picker is cleaned up
even when the tracker is empty).

State is bot-process-local (a single aiogram polling process), so a plain dict
keyed by ``chat_id`` is sufficient. It is not persisted: after a restart the
tracker is empty, so the first reply-button tap cannot delete the pre-restart
screen (one harmless orphan) and normal single-screen behavior resumes from the
next tap.
"""

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)

from .rich import answer_rich

logger = logging.getLogger(__name__)

# Per-chat id of the last bot section-screen message.
_last_screen_message: dict[int, int] = {}

# Per-chat lock serializing screen replacement so rapid double-taps cannot race
# on the tracker (which would otherwise leak an undeleted screen).
_chat_locks: dict[int, asyncio.Lock] = {}


def _lock_for(chat_id: int) -> asyncio.Lock:
    """Return, creating on first use, the screen-replacement lock for a chat.

    Safe without its own guard: the get-or-create runs synchronously with no
    ``await`` in between, so the single-threaded event loop cannot interleave
    another coroutine and create a second lock for the same chat.

    Args:
        chat_id: Telegram chat id.

    Returns:
        The asyncio.Lock dedicated to this chat's screen lifecycle helpers.
    """
    lock = _chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
    return lock


async def _delete_silently(message: Message | InaccessibleMessage, message_id: int) -> bool:
    """Delete a message in the triggering message's chat if possible.

    Deletion is best-effort cleanup: the target may already be gone, be older
    than Telegram's 48-hour delete window, or be undeletable for lack of rights.
    None of these should disrupt sending the new screen, so any TelegramAPIError
    (covering both TelegramBadRequest and TelegramForbiddenError) is logged at
    debug level and ignored.

    Args:
        message: A message bound to the bot; its chat is the deletion target
            and its ``.bot`` performs the call.
        message_id: Id of the message to delete (in ``message.chat``).

    Returns:
        True if the message was deleted; False if it was skipped (no bound bot) or
        the delete failed (logged at debug level and swallowed).

    Raises:
        No Telegram API exceptions are propagated.
    """
    bot = message.bot
    if bot is None:  # Defensive: bot is always bound on a message inside a handler.
        return False
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message_id)
        return True
    except TelegramAPIError as e:
        logger.debug(
            f"Could not delete previous screen {message_id} in chat {message.chat.id}: {e}"
        )
        return False


async def _neutralize_keyboard(message: Message | InaccessibleMessage, message_id: int) -> None:
    """Strip a message's inline keyboard so its buttons can no longer fire.

    Fallback for a callback source that could not be deleted (e.g. older than
    Telegram's 48-hour delete window): editing the reply markup to ``None`` is not
    time-bounded the way deletion is, so it turns a live orphan picker into an
    inert text remnant. Best-effort — a failure (the message is also un-editable)
    is logged at warning level and swallowed.

    Args:
        message: A message bound to the bot; its chat hosts the target and its
            ``.bot`` performs the call.
        message_id: Id of the message whose inline keyboard to remove.

    Returns:
        None. Telegram API failures are logged at warning level and swallowed.

    Raises:
        No Telegram API exceptions are propagated.
    """
    bot = message.bot
    if bot is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=message_id, reply_markup=None
        )
    except TelegramAPIError as e:
        logger.warning(
            f"Could not neutralize stale picker {message_id} in chat {message.chat.id}: {e}"
        )


async def show_screen(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> Message:
    """Send a section screen, deleting this chat's previous one.

    Sends ``text`` as a reply to the triggering message, then best-effort deletes
    the screen previously sent to this chat (if any) so only one section screen
    stays live. The new screen is sent before the old one is removed, so the chat
    is never momentarily empty. The triggering (user) message is left untouched.

    Args:
        message: The incoming user message that triggered the screen.
        text: Screen rich-HTML body (newlines become <br>; sent as a rich message).
        reply_markup: Optional keyboard for the new screen.

    Returns:
        The Message sent by the bot, now recorded as this chat's tracked screen.

    Raises:
        TelegramAPIError: Propagated if sending the new screen fails.
    """
    chat_id = message.chat.id

    async with _lock_for(chat_id):
        sent = await answer_rich(message, text, reply_markup)

        # Commit the new screen to the tracker BEFORE the best-effort delete:
        # the delete can be interrupted (e.g. CancelledError on shutdown) and
        # would otherwise leave the freshly-sent screen untracked, orphaning it
        # on the next tap.
        previous_id = _last_screen_message.get(chat_id)
        _last_screen_message[chat_id] = sent.message_id

        if previous_id is not None and previous_id != sent.message_id:
            await _delete_silently(message, previous_id)

        return sent


async def reset_screen_from_callback(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> Message | None:
    """Drop a callback flow back to a fresh PERSISTENT screen.

    For a callback handler that ends an interaction by returning to a screen which
    must persist — typically the main menu re-sent after a language change to
    refresh the reply-keyboard labels (a reply keyboard cannot be edited in place).
    It sends that screen (works even for an ``InaccessibleMessage`` source — its
    ``answer`` shortcut still targets the chat), but unlike :func:`show_screen`
    does NOT track it: Telegram drops a reply keyboard when its carrier message is
    deleted, so the new screen must survive later navigation (like the ``/start``
    welcome). It then clears this chat's tracked section screen and best-effort
    removes BOTH that old tracked screen and the callback's own source picker.

    Cleaning up the source explicitly — not only the tracked one — matters because
    the tracker is process-local and empty after a restart: a pre-restart inline
    message is no longer the tracked id, so it would otherwise linger and stay
    interactive. If the source cannot be deleted (e.g. older than Telegram's
    48-hour delete window), its inline keyboard is stripped instead so its buttons
    can no longer fire.

    Args:
        callback: The callback query whose flow is being reset.
        text: Screen rich-HTML body (newlines become <br>; sent as a rich message).
        reply_markup: Optional keyboard for the new (persistent) screen.

    Returns:
        The Message sent by the bot (a persistent, untracked screen), or None if
        the callback carries no message to target.

    Raises:
        TelegramAPIError: Propagated if sending the replacement screen fails.
    """
    message = callback.message
    if message is None:
        return None

    chat_id = message.chat.id

    async with _lock_for(chat_id):
        sent = await answer_rich(message, text, reply_markup)

        # The new screen is a PERSISTENT anchor: it carries the reply keyboard,
        # which Telegram drops if its carrier message is deleted, so it must NOT be
        # tracked (later navigation would delete it). Clear any tracked section
        # screen for this chat instead; the anchor then survives like /start.
        previous_id = _last_screen_message.pop(chat_id, None)

        # Remove the old tracked screen AND the callback's source picker so no
        # stale/interactive remnant is left. They coincide in the normal
        # in-place-edit flow but diverge when the tracker is stale (e.g. after a
        # restart); None and the just-sent id are skipped, each id handled once.
        seen: set[int] = set()
        for message_id in (previous_id, message.message_id):
            if message_id is None or message_id == sent.message_id or message_id in seen:
                continue
            seen.add(message_id)
            deleted = await _delete_silently(message, message_id)
            # The callback's own source is the interactive picker; if it could not
            # be deleted (e.g. older than Telegram's 48h delete window), strip its
            # inline keyboard so its buttons can't keep firing.
            if not deleted and message_id == message.message_id:
                await _neutralize_keyboard(message, message_id)

        return sent
