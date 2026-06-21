"""Single-screen UI helper for reply-keyboard navigation.

Reply-keyboard taps arrive as new user messages, so each section handler would
otherwise send a brand-new bot message and leave the previous one behind, piling
up duplicate screens in the chat. This helper keeps at most one live "section
screen" per chat: it sends the new screen first, then best-effort deletes the
previously tracked bot screen.

Intentional scope (decided with the user):
- The user's own tap messages are left in place (only the bot's previous screen
  is removed).
- The ``/start`` welcome is never tracked here, so it persists across navigation.

Inline-keyboard navigation inside a screen is unaffected: those handlers edit the
message in place (see ``safe_edit_message``), which preserves the tracked
``message_id``, so a later reply-button tap still deletes the right message.

State is bot-process-local (a single aiogram polling process), so a plain dict
keyed by ``chat_id`` is sufficient. It is not persisted: after a restart the
tracker is empty, so the first tap cannot delete the pre-restart screen (one
harmless orphan) and normal single-screen behavior resumes from the next tap.
"""

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

# Per-chat id of the last bot section-screen message.
_last_screen_message: dict[int, int] = {}

# Per-chat lock serializing show_screen so rapid double-taps cannot race on the
# tracker (which would otherwise leak an undeleted screen).
_chat_locks: dict[int, asyncio.Lock] = {}


def _lock_for(chat_id: int) -> asyncio.Lock:
    """Return (creating on first use) the show_screen lock for a chat.

    Safe without its own guard: the get-or-create runs synchronously with no
    ``await`` in between, so the single-threaded event loop cannot interleave
    another coroutine and create a second lock for the same chat.

    Args:
        chat_id: Telegram chat id.

    Returns:
        The asyncio.Lock dedicated to this chat.
    """
    lock = _chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
    return lock


async def _delete_silently(message: Message, message_id: int) -> None:
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
        None. Telegram API deletion failures are logged at debug level and
        swallowed.
    """
    bot = message.bot
    if bot is None:  # Defensive: bot is always bound on a message inside a handler.
        return
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message_id)
    except TelegramAPIError as e:
        logger.debug(
            f"Could not delete previous screen {message_id} in chat {message.chat.id}: {e}"
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
        text: Screen text (HTML, per the bot's default parse mode).
        reply_markup: Optional keyboard for the new screen.

    Returns:
        The Message sent by the bot, now recorded as this chat's tracked screen.
    """
    chat_id = message.chat.id

    async with _lock_for(chat_id):
        sent = await message.answer(text, reply_markup=reply_markup)

        # Commit the new screen to the tracker BEFORE the best-effort delete:
        # the delete can be interrupted (e.g. CancelledError on shutdown) and
        # would otherwise leave the freshly-sent screen untracked, orphaning it
        # on the next tap.
        previous_id = _last_screen_message.get(chat_id)
        _last_screen_message[chat_id] = sent.message_id

        if previous_id is not None and previous_id != sent.message_id:
            await _delete_silently(message, previous_id)

        return sent
