"""Formatters for the monitoring router."""

import logging
from datetime import datetime, timezone
from typing import Any
from multiprocessing.managers import DictProxy

from ...models import Server, PingStatistics, PingResult
from ...storage import SqliteStatisticsRepository
from ...storage.balance import BalanceRepository
from ...providers.manager import ProviderManager
from ..i18n import _
from .common import format_number, esc
from .balance import collect_provider_balances

logger = logging.getLogger(__name__)


def format_monitoring_dashboard(
    servers: list[Server],
    shared_state: DictProxy,
    stats_repo: SqliteStatisticsRepository,
    balance_repo: BalanceRepository | None = None,
    provider_manager: ProviderManager | None = None,
) -> str:
    """
    Format a dashboard with the overall monitoring statistics.

    Args:
        servers: List of servers.
        shared_state: Shared state of the servers.
        stats_repo: Statistics repository.
        balance_repo: Balance repository (optional).
        provider_manager: Provider manager (optional).

    Returns:
        Formatted dashboard text in the active language.
    """
    if not servers:
        return _("mon.empty")

    # Count the server statuses
    total_servers = len(servers)
    online_count = 0
    offline_count = 0
    unknown_count = 0

    # Per-provider statistics
    provider_stats: dict[str, dict[str, int]] = {}

    for server in servers:
        # Read the status from shared_state
        server_key = server.composite_key
        state = shared_state.get(server_key, {})
        status = state.get("status", "unknown")

        if status == "online":
            online_count += 1
        elif status == "offline":
            offline_count += 1
        else:
            unknown_count += 1

        # Count servers per provider_alias (effective_alias accounts for legacy)
        provider_key = server.effective_alias.upper()
        if provider_key not in provider_stats:
            provider_stats[provider_key] = {"total": 0, "online": 0, "offline": 0}
        provider_stats[provider_key]["total"] += 1
        if status == "online":
            provider_stats[provider_key]["online"] += 1
        elif status == "offline":
            provider_stats[provider_key]["offline"] += 1

    # Aggregate the overall ping statistics for the last 24 hours
    total_pings = 0
    successful_pings = 0
    failed_pings = 0
    timeout_pings = 0
    uptime_sum = 0.0
    servers_with_stats = 0

    for server in servers:
        stats_24h = stats_repo.get_recent_statistics(server.id, server.effective_alias, hours=24)
        if stats_24h and stats_24h.total_pings > 0:
            total_pings += stats_24h.total_pings
            successful_pings += stats_24h.successful_pings
            failed_pings += stats_24h.failed_pings
            timeout_pings += stats_24h.timeout_pings
            uptime_sum += stats_24h.uptime_percentage
            servers_with_stats += 1

    # Compute the average uptime
    avg_uptime = uptime_sum / servers_with_stats if servers_with_stats > 0 else 0.0

    # Build the message
    text = _("mon.dashboard_title") + "\n\n"

    # Section: general server information
    text += _("mon.section_servers") + "\n"
    text += _("mon.total_servers", count=total_servers) + "\n"
    text += _("mon.online", count=online_count) + "\n"
    text += _("mon.offline", count=offline_count) + "\n"
    text += _("mon.unknown", count=unknown_count) + "\n\n"

    # Section: finances (if data is available)
    if balance_repo is not None and provider_manager is not None:
        provider_balances = collect_provider_balances(balance_repo, provider_manager)

        # Keep only providers that support balance reporting
        providers_with_balance = [
            (name, data)
            for name, data in provider_balances.items()
            if data["supports_balance"]
        ]

        if providers_with_balance:
            # Sum the total balance (prepaid) and the expenses
            total_balance = 0.0
            total_expenses = 0.0

            for _name, data in providers_with_balance:
                balance = data.get("balance")
                billing_model = data.get("billing_model", "prepaid")

                if billing_model == "prepaid" and balance is not None:
                    total_balance += balance

                # Expenses: pending_charges (prepaid) or monthly_costs (postpaid)
                pending = data.get("pending_charges")
                monthly = data.get("monthly_costs")
                if pending is not None:
                    total_expenses += pending
                if monthly is not None:
                    total_expenses += monthly

            providers_count = len(providers_with_balance)
            text += _("mon.section_finance", count=providers_count) + "\n"
            text += _("mon.finance_balance", amount=total_balance) + "\n"
            text += _("mon.finance_expenses", amount=total_expenses) + "\n\n"

    # Section: ping statistics for the last 24 hours
    if total_pings > 0:
        text += _("mon.section_stats_24h") + "\n"
        text += _("mon.total_pings", value=format_number(total_pings)) + "\n"
        text += _("mon.successful", value=format_number(successful_pings)) + "\n"
        text += _("mon.errors", value=format_number(failed_pings)) + "\n"
        text += _("mon.timeout", value=format_number(timeout_pings)) + "\n"
        text += _("mon.avg_uptime", value=avg_uptime) + "\n\n"
    else:
        text += _("mon.section_stats_24h") + "\n"
        text += _("mon.no_ping_data") + "\n\n"

    # Section: per-provider statistics (counts only — no translatable words)
    if provider_stats:
        text += _("mon.section_by_provider") + "\n"
        for provider, stats in sorted(provider_stats.items()):
            text += f"<b>{esc(provider)}:</b> {stats['total']} "
            text += f"(🟢 {stats['online']} | 🔴 {stats['offline']})\n"

    return text


def format_servers_list(servers: list[Server], shared_state: DictProxy, page: int = 0) -> str:
    """
    Format the server list for display.

    Args:
        servers: List of servers.
        shared_state: Shared state of the servers.
        page: Page number.

    Returns:
        Formatted text with the server list in the active language.
    """
    if not servers:
        return _("mon.empty")

    # Count the statuses
    online_count = 0
    offline_count = 0
    unknown_count = 0

    for server in servers:
        server_key = server.composite_key
        state = shared_state.get(server_key, {})
        status = state.get("status", "unknown")

        if status == "online":
            online_count += 1
        elif status == "offline":
            offline_count += 1
        else:
            unknown_count += 1

    # Build the message
    text = _("mon.list_title") + "\n\n"

    # Overall statistics
    text += _("mon.total_servers", count=len(servers)) + "\n"
    text += (
        _("mon.status_inline", online=online_count, offline=offline_count, unknown=unknown_count)
        + "\n\n"
    )

    text += _("mon.choose_server")

    return text


def format_server_details(
    server: Server,
    state: dict[str, Any],
    stats_24h: PingStatistics | None,
    recent_errors: list[PingResult],
) -> str:
    """
    Format detailed information about a server.

    Args:
        server: Server.
        state: Current state from shared_state.
        stats_24h: Statistics for the last 24 hours.
        recent_errors: Recent errors (failed/timeout pings).

    Returns:
        Formatted text with the server details in the active language.
    """
    # Resolve the status
    status = state.get("status", "unknown")
    if status == "online":
        status_emoji = "✅"
        status_text = _("status.online")
    elif status == "offline":
        status_emoji = "❌"
        status_text = _("status.offline")
    else:
        status_emoji = "❓"
        status_text = _("status.unknown")

    # Basic information
    text = f"🖥️ <b>{esc(server.get_display_name())}</b>\n\n"
    text += _("details.status_label") + f" {status_emoji} {status_text}\n"
    text += _("details.provider_label") + f" {esc(server.effective_alias.upper())}\n"
    text += _("details.ip_label") + f" {esc(server.ip)}\n"

    if server.region:
        text += _("details.region_label") + f" {esc(server.region)}\n"
    if server.plan:
        text += _("details.plan_label") + f" {esc(server.plan)}\n"
    if server.os:
        text += _("details.os_label") + f" {esc(server.os)}\n"
    if server.vcpu_count and server.ram_mb:
        text += _("details.resources_label") + f" {server.vcpu_count} vCPU | {server.ram_mb} MB RAM"
        if server.disk_gb:
            text += f" | {server.disk_gb} " + _("details.disk_suffix")
        text += "\n"

    # Last ping information
    last_check = state.get("last_ping_time")
    if last_check:
        text += "\n" + _("details.last_ping_label") + f" {esc(last_check)}\n"

    response_time = state.get("response_time_ms")
    if response_time is not None:
        text += _("details.response_time_label") + f" {response_time:.2f} ms\n"

    monitoring_state = _("details.monitoring_on") if server.enabled else _("details.monitoring_off")
    text += _("details.monitoring_label") + f" {monitoring_state}\n"

    # Statistics for the last 24 hours
    if stats_24h:
        text += "\n" + _("details.stats_24h_header") + "\n\n"
        text += stats_24h.get_display_text()

    # Recent problems (errors only)
    if recent_errors:
        text += "\n\n" + _("details.recent_problems") + "\n"
        for error in recent_errors[:5]:
            # Format the elapsed time
            try:
                time_diff = datetime.now(timezone.utc) - error.timestamp
                if time_diff.total_seconds() < 60:
                    time_ago = _("time.just_now")
                elif time_diff.total_seconds() < 3600:
                    minutes = int(time_diff.total_seconds() / 60)
                    time_ago = _("time.min_ago", n=minutes)
                else:
                    hours = int(time_diff.total_seconds() / 3600)
                    time_ago = _("time.hours_ago", n=hours)
            except Exception as e:
                logger.warning(f"Failed to calculate time diff for error: {e}")
                time_ago = _("time.na")

            # Format the error status (technical enum value — not translated)
            status_text = error.status.value.upper()
            text += f"🔴 {status_text} ({time_ago})\n"

            # Show the error description if present
            if error.error:
                text += f"   {esc(error.error)}\n"

    return text


def format_statistics(
    server: Server,
    stats_1h: PingStatistics | None,
    stats_24h: PingStatistics | None,
    stats_7d: PingStatistics | None,
) -> str:
    """
    Format the full statistics across different periods.

    Args:
        server: Server.
        stats_1h: Statistics for the last 1 hour.
        stats_24h: Statistics for the last 24 hours.
        stats_7d: Statistics for the last 7 days.

    Returns:
        Formatted text with the statistics in the active language.
    """
    text = _("stats.title", name=esc(server.get_display_name())) + "\n\n"

    # For the last 1 hour
    if stats_1h:
        text += _("stats.section_1h") + "\n"
        text += stats_1h.get_display_text()
        text += "\n\n"
    else:
        text += _("stats.section_1h") + "\n"
        text += _("common.no_data") + "\n\n"

    # For the last 24 hours
    if stats_24h:
        text += _("stats.section_24h") + "\n"
        text += stats_24h.get_display_text()
        text += "\n\n"
    else:
        text += _("stats.section_24h") + "\n"
        text += _("common.no_data") + "\n\n"

    # For the last 7 days
    if stats_7d:
        text += _("stats.section_7d") + "\n"
        text += stats_7d.get_display_text()
    else:
        text += _("stats.section_7d") + "\n"
        text += _("common.no_data")

    return text
