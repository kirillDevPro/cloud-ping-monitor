"""Inline keyboards for the Telegram bot."""

import math

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from ...models import Server
from ..i18n import _, LANGUAGE_NAMES, SUPPORTED_LANGUAGES
from ..utils.callback_data import encode_callback_data


def _paginate(servers: list[Server], page: int, per_page: int) -> tuple[list[Server], int, int]:
    """Paginate a server list and clamp stale page indexes.

    The page index is clamped into [0, total_pages-1] so a stale page (e.g. servers
    were removed since the keyboard was rendered) never yields a blank page with a
    misleading counter. Callers MUST use the returned page for the nav row/buttons.

    Args:
        servers: Full server list to paginate.
        page: Requested zero-based page index.
        per_page: Number of servers per page.

    Returns:
        Tuple of (servers on the current page, total number of pages, clamped page).
    """
    total_pages = math.ceil(len(servers) / per_page)
    # total_pages can be 0 when the list is empty; clamp keeps page at 0 then.
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    return servers[start_idx : start_idx + per_page], total_pages, page


def _server_button_rows(
    page_servers: list[Server], encode_prefix: str
) -> list[list[InlineKeyboardButton]]:
    """Build one server button per row.

    Args:
        page_servers: Servers to render on the current page.
        encode_prefix: Callback prefix passed to encode_callback_data().

    Returns:
        Rows containing one status/name button per server.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for server in page_servers:
        # Use only the status emoji (✅/❌), without a server icon
        status_emoji = server.status.to_emoji() if hasattr(server, "status") else "❓"
        button_text = f"{status_emoji} {server.name}"
        callback_data = encode_callback_data(encode_prefix, server.composite_key)
        rows.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
    return rows


def _nav_row(
    page: int, total_pages: int, *, prev_cb: str, info_cb: str, next_cb: str
) -> list[InlineKeyboardButton] | None:
    """Build a pagination navigation row.

    Args:
        page: Current zero-based page index.
        total_pages: Total number of pages.
        prev_cb: Callback data for the previous-page button.
        info_cb: Callback data for the page indicator button.
        next_cb: Callback data for the next-page button.

    Returns:
        Navigation buttons, or None if there is only one page.
    """
    if total_pages <= 1:
        return None

    nav_buttons = [InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data=info_cb)]
    if page > 0:
        nav_buttons.insert(0, InlineKeyboardButton(text="◀️", callback_data=prev_cb))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=next_cb))
    return nav_buttons


def get_monitoring_keyboard(
    servers: list[Server], page: int = 0, per_page: int = 8
) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard with a paginated list of servers.

    Args:
        servers: List of servers to display.
        page: Current page number (zero-based).
        per_page: Number of servers per page.

    Returns:
        InlineKeyboardMarkup: Keyboard with the server list.
    """
    page_servers, total_pages, page = _paginate(servers, page, per_page)

    keyboard = _server_button_rows(page_servers, "monitor_details_")

    nav_row = _nav_row(
        page,
        total_pages,
        prev_cb=f"monitor_page_{page - 1}",
        info_cb="monitor_page_info",
        next_cb=f"monitor_page_{page + 1}",
    )
    if nav_row:
        keyboard.append(nav_row)

    # "Refresh" button
    keyboard.append(
        [InlineKeyboardButton(text=_("common.refresh"), callback_data=f"monitor_refresh_{page}")]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_server_details_keyboard(server_key: str) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for the server detail view.

    Args:
        server_key: Composite server key in the format "provider:server_id".

    Returns:
        InlineKeyboardMarkup: Keyboard with the server action buttons.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                text=_("kb.statistics"),
                callback_data=encode_callback_data("monitor_stats_", server_key),
            )
        ],
        [
            InlineKeyboardButton(
                text=_("kb.manage"),
                callback_data=encode_callback_data("server_control_", server_key),
            )
        ],
        [InlineKeyboardButton(text=_("common.back"), callback_data="monitor_back")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_server_stats_keyboard(server_key: str) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for the statistics view.

    Args:
        server_key: Composite server key in the format "provider:server_id".

    Returns:
        InlineKeyboardMarkup: Keyboard with the statistics action buttons.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                text=_("common.refresh"),
                callback_data=encode_callback_data("monitor_stats_", server_key),
            )
        ],
        [
            InlineKeyboardButton(
                text=_("common.back"),
                callback_data=encode_callback_data("monitor_details_", server_key),
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_servers_management_keyboard(
    servers: list[Server], page: int = 0, per_page: int = 8, provider: str | None = None
) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard with a paginated list of servers for management.

    Args:
        servers: List of servers to display.
        page: Current page number (zero-based).
        per_page: Number of servers per page.
        provider: Provider alias (used in navigation callback_data).

    Returns:
        InlineKeyboardMarkup: Keyboard with the server list.
    """
    page_servers, total_pages, page = _paginate(servers, page, per_page)

    keyboard = _server_button_rows(page_servers, "server_control_")

    # An alias may contain "_", so the navigation callbacks are built with the provider in mind
    nav_row = _nav_row(
        page,
        total_pages,
        prev_cb=f"servers_page_{provider}_{page - 1}" if provider else f"servers_page_{page - 1}",
        info_cb="servers_page_info",
        next_cb=f"servers_page_{provider}_{page + 1}" if provider else f"servers_page_{page + 1}",
    )
    if nav_row:
        keyboard.append(nav_row)

    # Action buttons (Back to providers + Refresh)
    action_buttons = []

    # "Back to providers" button (only when a provider is selected)
    if provider:
        action_buttons.append(
            InlineKeyboardButton(text=_("kb.back_to_providers"), callback_data="servers_back")
        )

    # "Refresh" button
    refresh_data = f"servers_refresh_{provider}_{page}" if provider else f"servers_refresh_{page}"
    action_buttons.append(InlineKeyboardButton(text=_("common.refresh"), callback_data=refresh_data))

    keyboard.append(action_buttons)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_server_control_keyboard(
    server_key: str, power_status: str | None = None, supports_graceful: bool = False
) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for managing a server.

    Buttons are rendered dynamically depending on power_status in a 2x2 + Back layout:
    - "running": [Restart | Stop] / [Refresh] / [Back]
    - "stopped": [Start] / [Refresh] / [Back]
    - "pending" or None: [Start | Stop] / [Restart | Refresh] / [Back]

    If supports_graceful=True and the server is not stopped, a separate row with a
    graceful shutdown (ACPI) button is added before the "Back" button.

    Args:
        server_key: Composite server key in the format "provider:server_id".
        power_status: Server status from the provider API ("running", "stopped", "pending").
        supports_graceful: Whether the provider supports graceful shutdown (ACPI).

    Returns:
        InlineKeyboardMarkup: Keyboard with the management buttons.
    """
    keyboard = []

    # Decide which operations are available based on the status
    if power_status == "running":
        # Server is running - row 1: [Restart | Stop]
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("kb.restart"),
                    callback_data=encode_callback_data("server_reboot_", server_key),
                ),
                InlineKeyboardButton(
                    text=_("kb.stop"),
                    callback_data=encode_callback_data("server_stop_", server_key),
                ),
            ]
        )
        # Row 2: [Refresh]
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("common.refresh"),
                    callback_data=encode_callback_data("server_refresh_", server_key),
                )
            ]
        )
    elif power_status == "stopped":
        # Server is stopped - row 1: [Start]
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("kb.start"),
                    callback_data=encode_callback_data("server_start_", server_key),
                )
            ]
        )
        # Row 2: [Refresh]
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("common.refresh"),
                    callback_data=encode_callback_data("server_refresh_", server_key),
                )
            ]
        )
    else:
        # Unknown status or pending - row 1: [Start | Stop]
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("kb.start"),
                    callback_data=encode_callback_data("server_start_", server_key),
                ),
                InlineKeyboardButton(
                    text=_("kb.stop"),
                    callback_data=encode_callback_data("server_stop_", server_key),
                ),
            ]
        )
        # Row 2: [Restart | Refresh]
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("kb.restart"),
                    callback_data=encode_callback_data("server_reboot_", server_key),
                ),
                InlineKeyboardButton(
                    text=_("common.refresh"),
                    callback_data=encode_callback_data("server_refresh_", server_key),
                ),
            ]
        )

    # Graceful shutdown — a separate row (only when the provider supports it
    # and the server is not stopped; the operation is meaningless for a stopped server)
    if supports_graceful and power_status != "stopped":
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("kb.shutdown_acpi"),
                    callback_data=encode_callback_data("server_shutdown_", server_key),
                )
            ]
        )

    # [Back] row
    keyboard.append([InlineKeyboardButton(text=_("common.back"), callback_data="servers_back")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_confirmation_keyboard(action: str, server_key: str) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for confirming a critical operation.

    Args:
        action: Operation type ("stop", "reboot", or "shutdown").
        server_key: Composite server key in the format "provider:server_id".

    Returns:
        InlineKeyboardMarkup: Keyboard with the confirmation buttons.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                text=_("kb.confirm"),
                callback_data=encode_callback_data(f"server_confirm_{action}_", server_key),
            )
        ],
        [
            InlineKeyboardButton(
                text=_("kb.cancel"),
                callback_data=encode_callback_data(f"server_cancel_{action}_", server_key),
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_balance_main_keyboard(provider_balances: dict[str, dict]) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for the main balance screen.

    Buttons are sorted: providers with an available balance first, then the
    unavailable ones (supports_balance=False).

    Args:
        provider_balances: Provider data dictionary from collect_provider_balances()
            {alias: {"name": str, "supports_balance": bool, ...}}.

    Returns:
        InlineKeyboardMarkup: Keyboard with the provider selection buttons.
    """
    # Split the providers into two groups
    available: list[tuple[str, str]] = []  # supports_balance=True
    unavailable: list[tuple[str, str]] = []  # supports_balance=False

    for alias, data in provider_balances.items():
        name = data["name"]
        if data["supports_balance"]:
            available.append((alias, name))
        else:
            unavailable.append((alias, name))

    # Combine: available first, then unavailable
    sorted_providers = available + unavailable

    # Build buttons, 2 per row
    keyboard: list[list[InlineKeyboardButton]] = []
    provider_buttons: list[InlineKeyboardButton] = []

    for alias, name in sorted_providers:
        provider_buttons.append(
            InlineKeyboardButton(
                text=f"🌍 {name}",
                callback_data=f"balance_provider_{alias}",
            )
        )

        # Append a row once 2 buttons have accumulated
        if len(provider_buttons) == 2:
            keyboard.append(provider_buttons)
            provider_buttons = []

    # Append the remaining buttons (if the count is odd)
    if provider_buttons:
        keyboard.append(provider_buttons)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_balance_history_keyboard(
    period: int = 30, provider_alias: str | None = None
) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for the balance history screen.

    Args:
        period: Current period in days (7 or 30).
        provider_alias: Provider alias to filter by (hetzner_prod, vultr_main, etc.).
                        If None, shows the history of all providers.

    Returns:
        InlineKeyboardMarkup: Keyboard with period toggles and a "Back" button.
    """
    keyboard = []

    # Build the provider suffix for callback_data
    provider_suffix = f":{provider_alias}" if provider_alias else ""

    # Period toggle buttons
    period_buttons = []

    # "7 days" button
    if period == 7:
        # Current period - shown as selected
        period_buttons.append(
            InlineKeyboardButton(
                text=f"• {_('kb.period_7')} •", callback_data=f"balance_history_7{provider_suffix}"
            )
        )
    else:
        period_buttons.append(
            InlineKeyboardButton(
                text=_("kb.period_7"), callback_data=f"balance_history_7{provider_suffix}"
            )
        )

    # "30 days" button
    if period == 30:
        # Current period - shown as selected
        period_buttons.append(
            InlineKeyboardButton(
                text=f"• {_('kb.period_30')} •",
                callback_data=f"balance_history_30{provider_suffix}",
            )
        )
    else:
        period_buttons.append(
            InlineKeyboardButton(
                text=_("kb.period_30"), callback_data=f"balance_history_30{provider_suffix}"
            )
        )

    keyboard.append(period_buttons)

    # "Back" button - returns to the provider or to the main screen
    if provider_alias:
        back_callback = f"balance_provider_{provider_alias}"
    else:
        back_callback = "balance_back_to_main"

    keyboard.append([InlineKeyboardButton(text=_("common.back"), callback_data=back_callback)])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_balance_settings_keyboard() -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for the balance settings screen.

    Returns:
        InlineKeyboardMarkup: Keyboard with a "Back" button.
    """
    keyboard = [[InlineKeyboardButton(text=_("common.back"), callback_data="balance_back_to_main")]]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_balance_provider_keyboard(provider_alias: str) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for the provider detail view.

    Args:
        provider_alias: Provider alias (hetzner_prod, vultr_main, etc.).

    Returns:
        InlineKeyboardMarkup: Keyboard with "History" and "Back" buttons.
    """
    keyboard = [
        [InlineKeyboardButton(text=_("kb.history"), callback_data=f"balance_history:{provider_alias}")],
        [InlineKeyboardButton(text=_("common.back"), callback_data="balance_back_to_main")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_provider_selection_keyboard(servers: list[Server]) -> InlineKeyboardMarkup:
    """
    Return an inline keyboard for selecting a provider.

    Shows providers grouped by provider_alias. Each button contains the provider
    alias and the number of servers.

    Args:
        servers: List of all servers.

    Returns:
        InlineKeyboardMarkup: Keyboard with the provider buttons.
    """
    # Count servers per provider_alias (effective_alias accounts for legacy)
    alias_counts: dict[str, int] = {}
    for server in servers:
        alias = server.effective_alias
        alias_counts[alias] = alias_counts.get(alias, 0) + 1

    # Build the keyboard
    keyboard = []

    # Add a button for each alias that has servers
    for alias, count in sorted(alias_counts.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            # Format the label: hetzner_prod -> HETZNER_PROD
            button_text = f"☁️ {alias.upper()} ({count})"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=button_text,
                        callback_data=f"provider_select_{alias}",
                    )
                ]
            )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_language_keyboard(current_language: str) -> InlineKeyboardMarkup:
    """
    Build the language-picker inline keyboard.

    One button per supported language; the active language is marked with bullets.
    Language names are proper nouns (🇬🇧 English / 🇷🇺 Русский) shown identically in
    every UI language, so they are not translated. The callback encodes the target
    language code (``set_lang_en`` / ``set_lang_ru``).

    Args:
        current_language: The user's currently active language code.

    Returns:
        InlineKeyboardMarkup: One row per supported language.
    """
    keyboard: list[list[InlineKeyboardButton]] = []
    for language in SUPPORTED_LANGUAGES:
        label = LANGUAGE_NAMES[language]
        if language == current_language:
            label = f"• {label} •"
        keyboard.append(
            [InlineKeyboardButton(text=label, callback_data=f"set_lang_{language}")]
        )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
