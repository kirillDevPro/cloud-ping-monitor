"""Router for the /start command."""

import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..i18n import _
from ..keyboards.reply import get_main_menu_keyboard
from ..utils import answer_rich

logger = logging.getLogger(__name__)

# Create the router for the /start command
start_router = Router(name="start")


@start_router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """
    Handle the /start command.

    Sends a welcome message and displays the main menu in the user's language
    (resolved by LanguageMiddleware; new users default to English).

    Args:
        message: Incoming message carrying the /start command.

    Returns:
        None.
    """
    await answer_rich(message, _("start.welcome"), get_main_menu_keyboard())
