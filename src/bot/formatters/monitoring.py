"""Formatters for the monitoring router."""

import logging
from datetime import datetime, timezone
from typing import Any
from multiprocessing.managers import DictProxy

from ...models import Server, PingStatistics, PingResult
from ...storage import SqliteStatisticsRepository
from ...storage.balance import BalanceRepository
from ...providers.manager import ProviderManager
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
        Formatted dashboard text.
    """
    if not servers:
        return (
            "📊 <b>Мониторинг серверов</b>\n\n"
            "Список серверов пуст.\n"
            "Добавьте серверы для мониторинга."
        )

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
    text = "📊 <b>Общий мониторинг серверов</b>\n\n"

    # Section: general server information
    text += "━━━ <b>Серверы</b> ━━━\n"
    text += f"<b>Всего серверов:</b> {total_servers}\n"
    text += f"🟢 Онлайн: {online_count}\n"
    text += f"🔴 Офлайн: {offline_count}\n"
    text += f"❓ Неизвестно: {unknown_count}\n\n"

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

            for _, data in providers_with_balance:
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
            text += f"━━━ <b>Финансы</b> ({providers_count} пров.) ━━━\n"
            text += f"💰 Баланс: ${total_balance:,.2f}\n"
            text += f"📉 Расходы/мес: ${total_expenses:,.2f}\n\n"

    # Section: ping statistics for the last 24 hours
    if total_pings > 0:
        text += "━━━ <b>Статистика за 24 часа</b> ━━━\n"
        text += f"<b>Всего пингов:</b> {format_number(total_pings)}\n"
        text += f"<b>Успешно:</b> {format_number(successful_pings)} 🟢\n"
        text += f"<b>Ошибки:</b> {format_number(failed_pings)} 🔴\n"
        text += f"<b>Timeout:</b> {format_number(timeout_pings)} ⏱️\n"
        text += f"<b>Средний Uptime:</b> {avg_uptime:.2f}%\n\n"
    else:
        text += "━━━ <b>Статистика за 24 часа</b> ━━━\n"
        text += "Нет данных о пингах\n\n"

    # Section: per-provider statistics
    if provider_stats:
        text += "━━━ <b>По провайдерам</b> ━━━\n"
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
        Formatted text with the server list.
    """
    if not servers:
        return (
            "📊 <b>Мониторинг серверов</b>\n\n"
            "Список серверов пуст.\n"
            "Добавьте серверы для мониторинга."
        )

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
    text = "📊 <b>Мониторинг серверов</b>\n\n"

    # Overall statistics
    text += f"<b>Всего серверов:</b> {len(servers)}\n"
    text += f"🟢 Онлайн: {online_count} | "
    text += f"🔴 Офлайн: {offline_count} | "
    text += f"❓ Неизвестно: {unknown_count}\n\n"

    text += "Выберите сервер для просмотра деталей 👇"

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
        Formatted text with the server details.
    """
    # Resolve the status
    status = state.get("status", "unknown")
    if status == "online":
        status_emoji = "✅"
        status_text = "ONLINE"
    elif status == "offline":
        status_emoji = "❌"
        status_text = "OFFLINE"
    else:
        status_emoji = "❓"
        status_text = "UNKNOWN"

    # Basic information
    text = f"🖥️ <b>{esc(server.get_display_name())}</b>\n\n"
    text += f"<b>Статус:</b> {status_emoji} {status_text}\n"
    text += f"<b>Провайдер:</b> {esc(server.effective_alias.upper())}\n"
    text += f"<b>IP:</b> {esc(server.ip)}\n"

    if server.region:
        text += f"<b>Регион:</b> {esc(server.region)}\n"
    if server.plan:
        text += f"<b>План:</b> {esc(server.plan)}\n"
    if server.os:
        text += f"<b>ОС:</b> {esc(server.os)}\n"
    if server.vcpu_count and server.ram_mb:
        text += f"<b>Ресурсы:</b> {server.vcpu_count} vCPU | {server.ram_mb} MB RAM"
        if server.disk_gb:
            text += f" | {server.disk_gb} GB Диск"
        text += "\n"

    # Last ping information
    last_check = state.get("last_ping_time")
    if last_check:
        text += f"\n<b>Последний ping:</b> {esc(last_check)}\n"

    response_time = state.get("response_time_ms")
    if response_time is not None:
        text += f"<b>Время отклика:</b> {response_time:.2f} ms\n"

    text += f"<b>Мониторинг:</b> {'Включён' if server.enabled else 'Выключен'}\n"

    # Statistics for the last 24 hours
    if stats_24h:
        text += "\n📊 <b>Статистика за 24 часа</b>\n\n"
        text += stats_24h.get_display_text()

    # Recent problems (errors only)
    if recent_errors:
        text += "\n\n🔴 <b>Последние проблемы:</b>\n"
        for error in recent_errors[:5]:
            # Format the elapsed time
            try:
                time_diff = datetime.now(timezone.utc) - error.timestamp
                if time_diff.total_seconds() < 60:
                    time_ago = "только что"
                elif time_diff.total_seconds() < 3600:
                    minutes = int(time_diff.total_seconds() / 60)
                    time_ago = f"{minutes} мин назад"
                else:
                    hours = int(time_diff.total_seconds() / 3600)
                    time_ago = f"{hours} ч назад"
            except Exception as e:
                logger.warning(f"Failed to calculate time diff for error: {e}")
                time_ago = "н/д"

            # Format the error status
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
        Formatted text with the statistics.
    """
    text = f"📊 <b>Статистика: {esc(server.get_display_name())}</b>\n\n"

    # For the last 1 hour
    if stats_1h:
        text += "━━━ <b>За 1 час</b> ━━━\n"
        text += stats_1h.get_display_text()
        text += "\n\n"
    else:
        text += "━━━ <b>За 1 час</b> ━━━\n"
        text += "Нет данных\n\n"

    # For the last 24 hours
    if stats_24h:
        text += "━━━ <b>За 24 часа</b> ━━━\n"
        text += stats_24h.get_display_text()
        text += "\n\n"
    else:
        text += "━━━ <b>За 24 часа</b> ━━━\n"
        text += "Нет данных\n\n"

    # For the last 7 days
    if stats_7d:
        text += "━━━ <b>За 7 дней</b> ━━━\n"
        text += stats_7d.get_display_text()
    else:
        text += "━━━ <b>За 7 дней</b> ━━━\n"
        text += "Нет данных"

    return text
