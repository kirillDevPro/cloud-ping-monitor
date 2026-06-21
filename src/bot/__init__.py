"""Telegram bot package for server monitoring; re-exports the bot/dispatcher factories."""

from .app import create_bot, create_dispatcher

__all__ = ["create_bot", "create_dispatcher"]
