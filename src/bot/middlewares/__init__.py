"""Middleware for the Telegram bot."""

from .admin import AdminCheckMiddleware

__all__ = ["AdminCheckMiddleware"]
