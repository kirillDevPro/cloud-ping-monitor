"""Middleware that resolves the user's UI language for each update.

Registered AFTER :class:`~src.bot.middlewares.admin.AdminCheckMiddleware` so it
runs only for admins that passed the access check. For every Message/CallbackQuery
it looks up the sender's stored language (defaulting to English) and:

* activates it via :func:`~src.bot.i18n.set_current_language` so the request-scoped
  ``_('key')`` calls in handlers, keyboards, and formatters resolve to that
  language without taking a ``language`` parameter; and
* injects ``data['language']`` so a handler can read the resolved code explicitly
  (e.g. the settings screen, which highlights the current choice).

The active language lives in a :class:`contextvars.ContextVar`; setting it before
``await handler(...)`` makes it visible throughout the handler's call chain.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from ..i18n import DEFAULT_LANGUAGE, get_user_language, set_current_language

logger = logging.getLogger(__name__)


class LanguageMiddleware(BaseMiddleware):
    """Resolve and activate the sender's UI language for each update."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Activate the sender's language, then invoke the downstream handler.

        Args:
            handler: The downstream handler to invoke.
            event: The incoming event (TelegramObject).
            data: Context data passed to the handler; ``language`` is injected.

        Returns:
            The handler's result.
        """
        language = DEFAULT_LANGUAGE
        if isinstance(event, (Message, CallbackQuery)) and event.from_user is not None:
            language = get_user_language(event.from_user.id)

        set_current_language(language)
        data["language"] = language

        return await handler(event, data)
