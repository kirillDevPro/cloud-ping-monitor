"""Keyboards for the Telegram bot."""

from .reply import get_main_menu_keyboard
from .inline import (
    get_monitoring_keyboard,
    get_server_details_keyboard,
    get_server_stats_keyboard,
    get_servers_management_keyboard,
    get_server_control_keyboard,
    get_confirmation_keyboard,
    get_balance_main_keyboard,
    get_balance_history_keyboard,
    get_balance_settings_keyboard,
    get_balance_provider_keyboard,
    get_provider_selection_keyboard,
)

__all__ = [
    "get_main_menu_keyboard",
    "get_monitoring_keyboard",
    "get_server_details_keyboard",
    "get_server_stats_keyboard",
    "get_servers_management_keyboard",
    "get_server_control_keyboard",
    "get_confirmation_keyboard",
    "get_balance_main_keyboard",
    "get_balance_history_keyboard",
    "get_balance_settings_keyboard",
    "get_balance_provider_keyboard",
    "get_provider_selection_keyboard",
]
