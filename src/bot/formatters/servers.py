"""Formatters for the server-management router."""

from typing import Any

from ...models import Server, ServerStatus, PingStatistics, PingResult
from .common import esc


def format_provider_selection(servers: list[Server]) -> str:
    """
    Format the provider-selection screen.

    Args:
        servers: List of all servers.

    Returns:
        Formatted text for the provider-selection screen.
    """
    if not servers:
        return (
            "🖥️ <b>Управление серверами</b>\n\n"
            "Список серверов пуст.\n"
            "Добавьте серверы для управления."
        )

    # Compute overall counts
    online_count = sum(1 for s in servers if s.status == ServerStatus.ONLINE)
    offline_count = sum(1 for s in servers if s.status == ServerStatus.OFFLINE)
    unknown_count = len(servers) - online_count - offline_count

    # Build the message
    text = "🖥️ <b>Управление серверами</b>\n\n"

    # Overall stats (compact format)
    text += f"📊 {len(servers)} • 🟢{online_count} 🔴{offline_count} ❓{unknown_count}\n\n"

    text += "Выберите провайдера для просмотра серверов 👇"

    return text


def format_servers_management_list(
    servers: list[Server],
    page: int = 0,
    provider_alias: str | None = None,
) -> str:
    """
    Format the server list for the management screen.

    Args:
        servers: List of servers.
        page: Page number.
        provider_alias: Provider alias; when provided it is shown in the header.

    Returns:
        Formatted text with the list of servers.
    """
    if not servers:
        return (
            "🖥️ <b>Управление серверами</b>\n\n"
            "Список серверов пуст.\n"
            "Добавьте серверы для управления."
        )

    # Count statuses
    online_count = sum(1 for s in servers if s.status == ServerStatus.ONLINE)
    offline_count = sum(1 for s in servers if s.status == ServerStatus.OFFLINE)
    unknown_count = len(servers) - online_count - offline_count

    # Build the header
    if provider_alias:
        text = f"🖥️ <b>Серверы • {esc(provider_alias.upper())}</b>\n\n"
    else:
        text = "🖥️ <b>Управление серверами</b>\n\n"

    # Overall stats (compact format)
    text += f"📊 {len(servers)} • 🟢{online_count} 🔴{offline_count} ❓{unknown_count}\n\n"

    text += "Выберите сервер для управления 👇"

    return text


def format_server_control_details(
    server: Server,
    power_status: str | None = None,
    state: dict[str, Any] | None = None,
    stats_24h: PingStatistics | None = None,
    recent_errors: list[PingResult] | None = None,
) -> str:
    """
    Format the full server details for the management screen.

    Args:
        server: The server.
        power_status: Power status from the cloud provider API (if available).
        state: Current state from shared_state.
        stats_24h: Statistics for the last 24 hours.
        recent_errors: Recent errors (failed/timeout pings).

    Returns:
        Formatted text with the server details.
    """
    # Determine the monitoring status
    monitoring_status = "unknown"
    if state:
        monitoring_status = state.get("status", "unknown")

    if monitoring_status == "online":
        status_emoji = "✅"
        status_text = "ONLINE"
    elif monitoring_status == "offline":
        status_emoji = "❌"
        status_text = "OFFLINE"
    else:
        status_emoji = "❓"
        status_text = "UNKNOWN"

    text = f"🖥️ <b>{esc(server.name)}</b>\n\n"

    # Line 1: status and provider
    text += f"{status_emoji} {status_text} • {esc(server.effective_alias.upper())}\n"

    # Line 2: region and plan
    region_plan = []
    if server.region:
        region_plan.append(f"📍 {esc(server.region)}")
    if server.plan:
        region_plan.append(esc(server.plan))
    if region_plan:
        text += " • ".join(region_plan) + "\n"

    # Line 3: IP
    text += f"IP <code>{esc(server.ip)}</code>\n"

    # Line 4: response time (if present in shared_state)
    if state:
        response_time = state.get("response_time_ms")
        if response_time is not None:
            text += f"⚡ {response_time:.0f}ms\n"

    # Resources (compact, if available)
    if server.vcpu_count and server.ram_mb:
        resources = f"{server.vcpu_count}vCPU/{server.ram_mb}MB"
        if server.disk_gb:
            resources += f"/{server.disk_gb}GB"
        text += f"💾 {resources}\n"

    # Information from the provider API (compact)
    if power_status:
        # Pick an emoji for the status
        if power_status == "running":
            power_emoji = "✅"
            power_text = "ON"
        elif power_status == "stopped":
            power_emoji = "⏹️"
            power_text = "OFF"
        else:
            power_emoji = "⏳"
            power_text = esc(power_status.upper())

        text += f"\n🔌 {power_emoji} {power_text}\n"

    # Statistics for the last 24 hours (if available)
    if stats_24h and stats_24h.total_pings > 0:
        text += "\n📊 <b>Статистика за 24 часа</b>\n\n"
        text += stats_24h.get_display_text()

    # Recent errors (compact: at most 3 red markers on a single line)
    if recent_errors:
        shown = min(len(recent_errors), 3)
        text += "\n" + "🔴 " * shown
        if len(recent_errors) > 3:
            text += f"+{len(recent_errors) - 3}"

    return text


def format_confirmation_message(action: str, server: Server) -> str:
    """
    Format the operation-confirmation message.

    Args:
        action: Operation type ("stop", "reboot", or "shutdown").
        server: The server.

    Returns:
        Formatted confirmation text.
    """
    if action == "stop":
        text = "⚠️ <b>Подтверждение остановки</b>\n\n"
        text += "Вы действительно хотите остановить сервер:\n"
        text += f"<b>{esc(server.get_display_name())}</b> ({esc(server.ip)})\n\n"
        text += "⚠️ <b>Внимание:</b> Сервер будет недоступен до следующего запуска.\n"
        text += "Все запущенные процессы будут остановлены."
    elif action == "shutdown":
        text = "🌙 <b>Подтверждение выключения (ACPI)</b>\n\n"
        text += "Вы действительно хотите мягко выключить сервер:\n"
        text += f"<b>{esc(server.get_display_name())}</b> ({esc(server.ip)})\n\n"
        text += "ℹ️ <b>Graceful shutdown:</b> ОС получит сигнал на корректное завершение.\n"
        text += "Сервер будет недоступен до следующего запуска."
    elif action == "reboot":
        text = "⚠️ <b>Подтверждение перезагрузки</b>\n\n"
        text += "Вы действительно хотите перезагрузить сервер:\n"
        text += f"<b>{esc(server.get_display_name())}</b> ({esc(server.ip)})\n\n"
        text += "⚠️ <b>Внимание:</b> Сервер будет недоступен на время перезагрузки (1-2 минуты).\n"
        text += "Все активные подключения будут разорваны."
    else:
        text = "⚠️ <b>Подтверждение операции</b>\n\n"
        text += f"Сервер: <b>{esc(server.get_display_name())}</b>\n"
        text += f"Операция: {action}"

    return text


def format_operation_result(
    action: str, server_name: str, success: bool, error: str | None = None
) -> str:
    """
    Format the result of a completed operation.

    Args:
        action: Operation type ("start", "stop", "reboot", "shutdown").
        server_name: Server name.
        success: Whether the operation succeeded.
        error: Error text (if any).

    Returns:
        Formatted result text.
    """
    action_texts = {
        "start": ("запущен", "запуска"),
        "stop": ("остановлен", "остановки"),
        "reboot": ("перезагружен", "перезагрузки"),
        "shutdown": ("выключен", "выключения"),
    }

    success_text, error_text = action_texts.get(action, ("обработан", "операции"))

    if success:
        text = "✅ <b>Операция выполнена</b>\n\n"
        text += f"Сервер <b>{esc(server_name)}</b> {success_text}.\n\n"
        text += "ℹ️ <i>Изменения вступят в силу через 30-60 секунд.\n"
        text += "Используйте кнопку 'Обновить статус' для проверки.</i>"
    else:
        text = f"❌ <b>Ошибка {error_text}</b>\n\n"
        text += f"Не удалось выполнить операцию для сервера <b>{esc(server_name)}</b>.\n\n"
        if error:
            text += f"<b>Детали:</b> {esc(error)}"
        else:
            text += "Попробуйте позже или обратитесь к администратору."

    return text
