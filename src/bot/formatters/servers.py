"""Formatters for the server-management router."""

from typing import Any

from ...models import Server, ServerStatus, PingStatistics, PingResult
from ..i18n import _
from ..utils.rich import blocks, stack
from .common import esc, stats_metric_line, strip_rule


def format_provider_selection(servers: list[Server]) -> str:
    """
    Format the provider-selection screen.

    Args:
        servers: List of all servers.

    Returns:
        Formatted text for the provider-selection screen in the active language.
    """
    if not servers:
        return _("srv.empty")

    # Compute overall counts
    online_count = sum(1 for s in servers if s.status == ServerStatus.ONLINE)
    offline_count = sum(1 for s in servers if s.status == ServerStatus.OFFLINE)
    unknown_count = len(servers) - online_count - offline_count

    return blocks(
        _("srv.manage_title"),
        f"📊 {len(servers)} • 🟢{online_count} 🔴{offline_count} ❓{unknown_count}",
        _("srv.choose_provider"),
    )


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
        Formatted text with the list of servers in the active language.
    """
    if not servers:
        return _("srv.empty")

    # Count statuses
    online_count = sum(1 for s in servers if s.status == ServerStatus.ONLINE)
    offline_count = sum(1 for s in servers if s.status == ServerStatus.OFFLINE)
    unknown_count = len(servers) - online_count - offline_count

    if provider_alias:
        title = _("srv.servers_provider_title", provider=esc(provider_alias.upper()))
    else:
        title = _("srv.manage_title")

    return blocks(
        title,
        f"📊 {len(servers)} • 🟢{online_count} 🔴{offline_count} ❓{unknown_count}",
        _("srv.choose_server"),
    )


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
        Formatted text with the server details in the active language.
    """
    # Determine the monitoring status
    monitoring_status = "unknown"
    if state:
        monitoring_status = state.get("status", "unknown")

    if monitoring_status == "online":
        status_emoji = "✅"
        status_text = _("status.online")
    elif monitoring_status == "offline":
        status_emoji = "❌"
        status_text = _("status.offline")
    else:
        status_emoji = "❓"
        status_text = _("status.unknown")

    # Compact key:value lines for the management card.
    info_lines = [
        f"{status_emoji} {status_text} • {esc(server.effective_alias.upper())}",
    ]

    region_plan = []
    if server.region:
        region_plan.append(f"📍 {esc(server.region)}")
    if server.plan:
        region_plan.append(esc(server.plan))
    if region_plan:
        info_lines.append(" • ".join(region_plan))

    info_lines.append(f"IP <code>{esc(server.ip)}</code>")

    if state:
        response_time = state.get("response_time_ms")
        if response_time is not None:
            info_lines.append(f"⚡ {response_time:.0f}ms")

    if server.vcpu_count and server.ram_mb:
        resources = f"{server.vcpu_count}vCPU/{server.ram_mb}MB"
        if server.disk_gb:
            resources += f"/{server.disk_gb}GB"
        info_lines.append(f"💾 {resources}")

    # Power state from the provider API (compact).
    if power_status:
        if power_status == "running":
            power_emoji = "✅"
            power_text = _("power.on")
        elif power_status == "stopped":
            power_emoji = "⏹️"
            power_text = _("power.off")
        else:
            power_emoji = "⏳"
            power_text = esc(power_status.upper())
        info_lines.append(f"🔌 {power_emoji} {power_text}")

    sections: list[str] = [f"🖥️ <b>{esc(server.name)}</b>", stack(*info_lines)]

    # Statistics for the last 24 hours (if available) as a compact metric table —
    # the same card the monitoring detail shows, for a consistent look.
    if stats_24h and stats_24h.total_pings > 0:
        sections.append(
            stack(
                strip_rule(_("details.stats_24h_header")),
                stats_metric_line(stats_24h),
            )
        )

    # Recent errors (compact: at most 3 red markers on a single line).
    if recent_errors:
        markers = "🔴 " * min(len(recent_errors), 3)
        if len(recent_errors) > 3:
            markers += f"+{len(recent_errors) - 3}"
        sections.append(markers.strip())

    return blocks(*sections)


def format_confirmation_message(action: str, server: Server) -> str:
    """
    Format the operation-confirmation message.

    Args:
        action: Operation type ("stop", "reboot", or "shutdown").
        server: The server.

    Returns:
        Formatted confirmation text in the active language.
    """
    server_line = f"<b>{esc(server.get_display_name())}</b> ({esc(server.ip)})"

    if action == "stop":
        return blocks(
            _("srv.confirm_stop_title"),
            stack(_("srv.confirm_stop_q"), server_line),
            _("srv.confirm_stop_warn"),
        )
    if action == "shutdown":
        return blocks(
            _("srv.confirm_shutdown_title"),
            stack(_("srv.confirm_shutdown_q"), server_line),
            _("srv.confirm_shutdown_warn"),
        )
    if action == "reboot":
        return blocks(
            _("srv.confirm_reboot_title"),
            stack(_("srv.confirm_reboot_q"), server_line),
            _("srv.confirm_reboot_warn"),
        )
    return blocks(
        _("srv.confirm_generic_title"),
        stack(
            _("srv.confirm_generic_server", name=esc(server.get_display_name())),
            _("srv.confirm_generic_action", action=esc(action)),
        ),
    )


# Operation -> catalog keys for the result wording. The "done" word is a past
# participle used in the success body; the "err" word names the operation in the
# error title. Code-coupled action keys ("start"/"stop"/...) are NOT translated.
_ACTION_DONE_KEYS: dict[str, str] = {
    "start": "action.start.done",
    "stop": "action.stop.done",
    "reboot": "action.reboot.done",
    "shutdown": "action.shutdown.done",
}
_ACTION_ERR_KEYS: dict[str, str] = {
    "start": "action.start.err",
    "stop": "action.stop.err",
    "reboot": "action.reboot.err",
    "shutdown": "action.shutdown.err",
}


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
        Formatted result text in the active language.
    """
    done_word = _(_ACTION_DONE_KEYS.get(action, "action.generic.done"))
    err_word = _(_ACTION_ERR_KEYS.get(action, "action.generic.err"))

    if success:
        return blocks(
            _("srv.op_success_title"),
            _("srv.op_success_body", name=esc(server_name), action=done_word),
            _("srv.op_success_hint"),
        )

    sections: list[str] = [
        _("srv.op_error_title", action=err_word),
        _("srv.op_error_body", name=esc(server_name)),
    ]
    if error:
        sections.append(_("srv.op_error_details", error=esc(error)))
    else:
        sections.append(_("srv.op_error_retry"))
    return blocks(*sections)
