"""Formatters for the balance router."""

from ...config import Settings
from ...storage.balance import (
    BalanceRecord,
    BalanceRepository,
    PostpaidBalanceRecord,
    PrepaidBalanceRecord,
)
from ...providers.manager import ProviderManager
from ..i18n import _, plural
from .common import format_days_as_period, esc


def collect_provider_balances(
    balance_repo: BalanceRepository,
    provider_manager: ProviderManager,
) -> dict[str, dict]:
    """
    Collect balance information for all providers.

    Removes duplication between cmd_balance and callback_balance_back_to_main.

    Args:
        balance_repo: Balance repository
        provider_manager: Provider manager

    Returns:
        Dict with information about each provider:
        {
            "hetzner_prod": {
                "emoji": "[H]",
                "name": "Hetzner (prod)",
                "balance": 123.45,
                "supports_balance": True,
                "billing_model": "prepaid",
                "pending_charges": 21.06,
                "monthly_costs": None,
            },
            ...
        }
    """
    provider_balances = {}
    providers = provider_manager.get_all_providers()

    # providers returns dict[str, tuple[BaseProvider, ProviderConfig]]
    for alias, (provider, config) in providers.items():
        # Look up the balance by alias (new format)
        latest_record = balance_repo.get_latest_record(provider_alias=alias)

        # Determine charges depending on the record type
        pending_charges: float | None = None
        monthly_costs: float | None = None

        if latest_record is not None:
            if isinstance(latest_record, PrepaidBalanceRecord):
                pending_charges = latest_record.pending_charges
            elif isinstance(latest_record, PostpaidBalanceRecord):
                monthly_costs = latest_record.monthly_costs

        provider_balances[alias] = {
            "emoji": provider.get_provider_emoji(),
            "name": provider.get_provider_display_name(),
            "balance": latest_record.display_value if latest_record else None,
            "supports_balance": provider.supports_balance(),
            "billing_model": provider.get_billing_model().value,
            "pending_charges": pending_charges,
            "monthly_costs": monthly_costs,
        }

    return provider_balances


def format_balance_main(provider_balances: dict) -> str:
    """
    Format the main balance screen - a summary across all providers.

    Groups by categories:
    1. Available funds (prepaid balances)
    2. Charges for the current month
    3. Unavailable providers

    Args:
        provider_balances: Dict with provider balances

    Returns:
        str: Formatted message in the active language
    """
    text = _("bal.main_title") + "\n"

    if not provider_balances:
        text += "\n" + _("bal.no_providers") + "\n\n"
        text += _("bal.add_api_keys")
        return text

    # Group providers by category
    available_balances: list[tuple[str, str, float]] = []  # (emoji, name, balance)
    postpaid_providers: list[tuple[str, str]] = []  # (emoji, name) - for the balances section
    monthly_expenses: list[tuple[str, str, float]] = []  # (emoji, name, amount)
    unavailable: list[tuple[str, str]] = []  # (emoji, name)

    for provider_data in provider_balances.values():
        emoji = provider_data["emoji"]
        name = provider_data["name"]
        balance = provider_data["balance"]
        supports_balance = provider_data["supports_balance"]
        billing_model = provider_data.get("billing_model", "prepaid")
        pending_charges = provider_data.get("pending_charges")
        monthly_costs = provider_data.get("monthly_costs")

        if not supports_balance:
            # Provider does not support the balance API
            unavailable.append((emoji, name))
        elif billing_model == "postpaid":
            # Postpaid (AWS) - no balance, only charges
            postpaid_providers.append((emoji, name))
            if monthly_costs is not None:
                monthly_expenses.append((emoji, name, monthly_costs))
        elif balance is not None:
            # Prepaid (Vultr) - has a balance
            available_balances.append((emoji, name, balance))
            if pending_charges is not None and pending_charges > 0:
                monthly_expenses.append((emoji, name, pending_charges))

    # Section 1: Available funds
    if available_balances or postpaid_providers:
        text += "\n" + _("bal.available_funds") + "\n"
        for emoji, name, balance in available_balances:
            text += f"{emoji} {esc(name)}: <b>${balance:,.2f}</b>\n"
        for emoji, name in postpaid_providers:
            text += f"{emoji} {esc(name)}: — <i>{_('bal.postpaid_suffix')}</i>\n"

    # Section 2: Charges for the current month
    if monthly_expenses:
        text += "\n" + _("bal.monthly_expenses") + "\n"
        for emoji, name, amount in monthly_expenses:
            text += f"{emoji} {esc(name)}: ${amount:,.2f}\n"

    # Section 3: Unavailable providers
    if unavailable:
        text += "\n" + _("bal.unavailable") + "\n"
        for emoji, name in unavailable:
            text += f"{emoji} {esc(name)} <i>{_('bal.no_api_suffix')}</i>\n"

    text += "\n" + _("bal.choose_provider")

    return text


def format_balance_history(
    records: list[BalanceRecord],
    period: int,
    provider_emojis: dict,
    provider_filter: str | None = None,
) -> str:
    """
    Format the balance history screen.

    Args:
        records: List of balance records for the period
        period: Period in days
        provider_emojis: Dict of provider emojis {provider_alias: emoji}
        provider_filter: Filter by provider (alias), or None for all

    Returns:
        str: Formatted message in the active language
    """
    if provider_filter:
        emoji = provider_emojis.get(provider_filter, "☁️")
        text = (
            _(
                "bal.history_title_provider",
                emoji=emoji,
                provider=esc(provider_filter),
                period=period,
            )
            + "\n\n"
        )
    else:
        text = _("bal.history_title_all", period=period) + "\n\n"

    if not records:
        text += _("bal.history_insufficient") + "\n\n"
        text += _("bal.history_wait")
        return text

    # Group records by date (last 10)
    # Sort from newest to oldest
    sorted_records = sorted(records, key=lambda r: r.timestamp, reverse=True)
    limited_records = sorted_records[:10]  # Show only the last 10

    for record in limited_records:
        # Use provider_alias if present, otherwise provider_type for legacy
        record_alias = record.provider_alias or record.provider_type
        emoji = provider_emojis.get(record_alias, "☁️")
        timestamp_str = record.timestamp.strftime("%Y-%m-%d %H:%M")

        text += f"<b>{timestamp_str}</b>\n"
        # Use display_value for a unified display
        text += f"{emoji} {esc(record_alias)}: {record.format_summary()}\n\n"

    if len(sorted_records) > 10:
        text += plural("bal.history_more", len(sorted_records) - 10) + "\n\n"

    if provider_filter:
        text += _("bal.history_only_provider", provider=esc(provider_filter))
    else:
        text += _("bal.history_all_providers")

    return text


def format_balance_settings(settings: Settings) -> str:
    """
    Format the balance settings screen.

    Args:
        settings: Application settings

    Returns:
        str: Formatted message in the active language
    """
    text = _("bal.settings_title") + "\n\n"

    # Notification threshold
    text += _("bal.settings_threshold", value=settings.BALANCE_THRESHOLD) + "\n"
    text += _("bal.settings_threshold_hint") + "\n\n"

    # Check interval
    interval_hours = settings.BALANCE_CHECK_INTERVAL / 3600
    text += _("bal.settings_interval", hours=interval_hours) + "\n"
    text += _("bal.settings_interval_hint") + "\n\n"

    # Hint
    text += _("bal.settings_how_to") + "\n"
    text += _("bal.settings_env_line") + "\n"
    text += f"   • <code>BALANCE_THRESHOLD={settings.BALANCE_THRESHOLD}</code>\n"
    text += f"   • <code>BALANCE_CHECK_INTERVAL={settings.BALANCE_CHECK_INTERVAL}</code>\n\n"
    text += _("bal.settings_restart")

    return text


# Balance-trend value -> (emoji, catalog key). The dict keys are code-coupled
# (returned by the burn-rate analysis); the words are translated.
_TREND_EMOJI: dict[str, str] = {
    "increasing": "📈",
    "decreasing": "📉",
    "stable": "➡️",
    "unknown": "❓",
}
_TREND_KEYS: dict[str, str] = {
    "increasing": "trend.increasing",
    "decreasing": "trend.decreasing",
    "stable": "trend.stable",
    "unknown": "trend.unknown",
}


def format_balance_provider_detail(
    provider_emoji: str,
    provider_name: str,
    record: BalanceRecord | None,
    burn_rate: float | None,
    days_left: float | None,
    trend: str,
    supports_balance: bool,
) -> str:
    """
    Format the detailed provider balance screen.

    Supports two billing models:
    - Prepaid (Vultr): shows the balance, pending charges, and burn metrics
    - Postpaid (AWS): shows the month's costs, without burn metrics

    Args:
        provider_emoji: Provider emoji
        provider_name: Provider name
        record: Balance record (None if not supported)
        burn_rate: Average spend per day (None if there is not enough data)
        days_left: Forecast of days until depletion
        trend: Balance change trend
        supports_balance: Whether the provider supports the balance API

    Returns:
        str: Formatted message in the active language
    """
    text = f"{provider_emoji} <b>{esc(provider_name)}</b>\n\n"

    if not supports_balance or record is None:
        text += _("bal.detail_unavailable") + "\n\n"
        text += _("bal.detail_no_api_body", provider=esc(provider_name)) + "\n\n"
        text += _("bal.detail_check_manually")
        return text

    # Use the record's billing_model property instead of checking fields
    if record.billing_model == "postpaid":
        # Postpaid provider (AWS): show the month's costs
        text += _("bal.detail_postpaid_costs", value=record.display_value) + "\n\n"
        text += _("bal.detail_postpaid_hint") + "\n\n"
    else:
        # Prepaid provider (Vultr): show the balance with a breakdown
        # Type narrowing: record is PrepaidBalanceRecord
        if isinstance(record, PrepaidBalanceRecord):
            text += _("bal.detail_available_balance", value=record.effective_balance) + "\n"
            text += _("bal.detail_account_balance", value=record.balance) + "\n"
            text += _("bal.detail_pending", value=record.pending_charges) + "\n\n"

            # Burn statistics
            if burn_rate is not None and burn_rate > 0:
                monthly_rate = burn_rate * 30
                text += _("bal.detail_burn", value=burn_rate) + "\n"
                text += _("bal.detail_burn_monthly", value=monthly_rate) + "\n"
            else:
                text += _("bal.detail_burn_insufficient") + "\n"
                text += _("bal.detail_burn_insufficient_hint") + "\n"

            if days_left is not None:
                if days_left > 0:
                    days_int = int(days_left)
                    period = format_days_as_period(days_int)
                    text += plural("bal.forecast_days", days_int) + "\n"
                    if period:
                        text += _("bal.detail_forecast_period", period=period) + "\n"
                else:
                    text += _("bal.detail_forecast_depleted") + "\n"
            else:
                text += _("bal.detail_forecast_none") + "\n"

            # Trend
            trend_emoji = _TREND_EMOJI.get(trend, "❓")
            trend_word = _(_TREND_KEYS.get(trend, "trend.unknown"))
            text += f"{trend_emoji} " + _("bal.detail_trend_label") + f" {trend_word}\n\n"

            # Last deposit information (prepaid only)
            if record.last_payment_date and record.last_payment_amount:
                text += _("bal.detail_last_deposit") + "\n"
                deposit_date = record.last_payment_date.strftime("%Y-%m-%d %H:%M")
                text += _("bal.detail_deposit_date", date=deposit_date) + "\n"
                text += _("bal.detail_deposit_amount", value=record.last_payment_amount) + "\n\n"

    # Check time (for all models)
    last_check = record.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    text += _("bal.detail_last_check", timestamp=last_check)

    return text
