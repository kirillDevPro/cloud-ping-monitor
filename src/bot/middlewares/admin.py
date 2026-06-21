"""Middleware that enforces administrator access control."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)


class AdminCheckMiddleware(BaseMiddleware):
    """
    Middleware that enforces administrator access control.

    Verifies that the user is in the list of allowed administrators.
    If the user is not an admin, it blocks access and sends an error message.
    """

    def __init__(self, admin_ids: list[int]):
        """
        Initialize the middleware.

        Args:
            admin_ids: List of allowed administrator user IDs.
        """
        self.admin_ids = admin_ids
        super().__init__()
        logger.info(f"AdminCheckMiddleware initialized with {len(admin_ids)} admin(s)")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """
        Check access rights before invoking the handler.

        Args:
            handler: The downstream handler to invoke.
            event: The incoming event (TelegramObject).
            data: Additional context data passed to the handler.

        Returns:
            The handler's result if access is granted, otherwise None.

        Raises:
            TelegramAPIError: If a Telegram API error occurs while processing.
            Exception: If any other unexpected error occurs.
        """
        try:
            # Check the event type and extract the user
            if not isinstance(event, (Message, CallbackQuery)):
                # Unknown event type - pass it through
                return await handler(event, data)

            user = event.from_user

            if user is None:
                logger.warning("Received event without user information")
                return

            # Administrator access check
            if user.id not in self.admin_ids:
                logger.warning(
                    f"Unauthorized access attempt: "
                    f"user_id={user.id}, "
                    f"username={user.username or 'N/A'}, "
                    f"first_name={user.first_name or 'N/A'}"
                )

                # Safely send the access-denied message
                try:
                    if isinstance(event, Message):
                        await event.answer(
                            "⛔️ <b>Доступ запрещён</b>\n\n"
                            "Этот бот доступен только администраторам."
                        )
                    elif isinstance(event, CallbackQuery):
                        await event.answer("⛔️ Доступ запрещён", show_alert=True)
                except TelegramAPIError as e:
                    logger.error(f"Failed to send unauthorized access message: {e}")

                return  # Block any further processing

            # The user is an administrator - pass the request through
            return await handler(event, data)

        except TelegramAPIError as e:
            logger.error(f"Telegram API error in admin middleware: {e}", exc_info=True)
            raise

        except Exception as e:
            logger.error(f"Unexpected error in admin middleware: {e}", exc_info=True)
            raise
