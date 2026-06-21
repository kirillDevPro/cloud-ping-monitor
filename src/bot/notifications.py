"""Functions for sending administrator notifications and reporting delivery status."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter

from .formatters.common import esc

logger = logging.getLogger(__name__)


async def _broadcast_to_admins(
    bot: Bot, admin_ids: list[int], message: str, *, log_label: str
) -> bool:
    """
    Broadcast a message to all administrators with uniform Telegram error handling.

    Each admin send is attempted independently. Telegram flood-control errors are retried
    once after the requested delay; all Telegram and unexpected client/session errors are
    logged and swallowed so notification failures do not crash background processors.

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        message: HTML text of the message
        log_label: Lowercase notification label used in logs
            (for example, "server down notification")

    Returns:
        bool: True if the message was delivered to at least one administrator,
            False if every send failed. ping_results_processor gates its anti-flap
            cooldown and last-notified state on this, so an undelivered down/up alert
            is retried on the next result instead of being silently consumed.
    """
    label_capitalized = log_label[:1].upper() + log_label[1:]
    delivered = False
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, message)
            logger.info(f"{label_capitalized} sent to admin {admin_id}")
            delivered = True
        except TelegramRetryAfter as e:
            # Flood control fires exactly when many alerts go out at once (e.g. a
            # provider outage downing several servers); wait out the window and
            # retry ONCE so a critical alert is not silently dropped.
            logger.warning(f"Rate limited for {admin_id}, retry after {e.retry_after}s")
            try:
                await asyncio.sleep(e.retry_after)
                await bot.send_message(admin_id, message)
                logger.info(f"{label_capitalized} sent to admin {admin_id} after retry")
                delivered = True
            except Exception as retry_error:
                logger.error(
                    f"Failed to resend {log_label} to {admin_id} after rate limit: {retry_error}",
                    exc_info=True,
                )
        except TelegramNetworkError as e:
            logger.error(f"Network error sending {log_label} to {admin_id}: {e}", exc_info=True)
        except TelegramAPIError as e:
            logger.error(
                f"Telegram API error sending {log_label} to {admin_id}: {e}", exc_info=True
            )
        except Exception as e:
            # Terminal catch-all: a NON-Telegram exception (ClientDecodeError on a
            # malformed/HTML gateway response — it subclasses AiogramError, not
            # TelegramAPIError — or a bare asyncio.TimeoutError / RuntimeError from the
            # aiohttp session layer) must never propagate to a caller. ping_results_processor
            # calls these sends inline, so an un-caught error here would otherwise kill it.
            logger.error(
                f"Unexpected error sending {log_label} to {admin_id}: {e}", exc_info=True
            )

    return delivered


async def send_server_down_notification(
    bot: Bot, admin_ids: list[int], server_name: str, server_ip: str, error: str | None
) -> bool:
    """
    Send a notification that a server has gone down.

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        server_name: Server name
        server_ip: Server IP address
        error: Error description, if any

    Returns:
        bool: True if delivered to at least one administrator (see _broadcast_to_admins).
    """
    message = (
        "🔴 <b>Сервер недоступен</b>\n\n"
        f"Сервер <b>{esc(server_name)}</b> ({esc(server_ip)}) перестал отвечать на ping."
    )

    if error:
        message += f"\n\n<b>Ошибка:</b> {esc(error)}"

    return await _broadcast_to_admins(bot, admin_ids, message, log_label="server down notification")


async def send_server_up_notification(
    bot: Bot,
    admin_ids: list[int],
    server_name: str,
    server_ip: str,
    response_time_ms: float | None,
) -> bool:
    """
    Send a notification that a server has recovered.

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        server_name: Server name
        server_ip: Server IP address
        response_time_ms: Response time in milliseconds

    Returns:
        bool: True if delivered to at least one administrator (see _broadcast_to_admins).
    """
    message = (
        "🟢 <b>Сервер восстановлен</b>\n\n"
        f"Сервер <b>{esc(server_name)}</b> ({esc(server_ip)}) снова доступен."
    )

    if response_time_ms is not None:
        message += f"\n\n<b>Время отклика:</b> {response_time_ms:.2f} ms"

    return await _broadcast_to_admins(bot, admin_ids, message, log_label="server up notification")


async def send_low_balance_notification(
    bot: Bot,
    admin_ids: list[int],
    balance: float,
    threshold: float,
    days_left: float | None,
    provider_name: str = "Unknown",
) -> None:
    """
    Send a low balance notification.

    Delivery failures are logged by _broadcast_to_admins() and not returned to the caller.

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        balance: Current balance in USD
        threshold: Threshold value in USD
        days_left: Forecast of days until depletion, if available
        provider_name: Provider name (VULTR, HETZNER, etc.)
    """
    safe_provider = esc(provider_name)
    message = (
        f"🔴 <b>Низкий баланс {safe_provider}</b>\n\n"
        f"Текущий баланс <b>${balance:.2f}</b> ниже порога <b>${threshold:.2f}</b>.\n\n"
    )

    if days_left is not None and days_left > 0:
        message += f"⏳ <b>Прогноз:</b> ~{int(days_left)} дней до исчерпания\n\n"
    elif days_left == 0:
        message += "⚠️ <b>Баланс исчерпан!</b>\n\n"

    message += f"💡 Пополните баланс в личном кабинете {safe_provider}."

    await _broadcast_to_admins(
        bot, admin_ids, message, log_label=f"low balance notification for {provider_name}"
    )


async def send_critical_error_notification(
    bot: Bot,
    admin_ids: list[int],
    error_type: str,
    error_message: str,
    details: dict | None = None,
) -> bool:
    """
    Send a critical error notification to administrators.

    Used to alert about critical problems that require immediate attention
    (for example, an invalid API token or access issues).

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        error_type: Error type (for example, "Vultr API", "Hetzner API", "Storage", "IPC")
        error_message: Error message text
        details: Additional error details (optional)

    Returns:
        bool: True if delivered to at least one administrator (see _broadcast_to_admins).
            Callers that retry until delivered (e.g. the stall watchdog) gate on this.
    """
    message = f"🔴 <b>Критическая ошибка: {esc(error_type)}</b>\n\n" f"{esc(error_message)}\n\n"

    if details:
        message += "<b>Детали:</b>\n"
        for key, value in details.items():
            # Do not expose sensitive data (tokens, passwords)
            if any(
                sensitive in key.lower() for sensitive in ["token", "key", "password", "secret"]
            ):
                value = "***скрыто***"
            message += f"• <code>{esc(key)}</code>: {esc(value)}\n"
        message += "\n"

    message += "⚠️ Проверьте логи приложения для дополнительной информации."

    return await _broadcast_to_admins(
        bot, admin_ids, message, log_label="critical error notification"
    )


async def send_provider_outage_notification(
    bot: Bot,
    admin_ids: list[int],
    provider_label: str,
    duration_text: str,
    failures: int,
    last_error: str,
) -> None:
    """
    Send a notification about a SUSTAINED transient provider outage.

    Unlike a critical error, this is used only when a transient failure
    (5xx, rate limit, network) lasts for several consecutive sync cycles and is
    clearly not an instantaneous API "blip". Sent once per outage period;
    recovery is reported via send_provider_recovered_notification.
    Delivery failures are logged by _broadcast_to_admins() and not returned.

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        provider_label: Human-readable provider name (display_name)
        duration_text: Outage duration (for example, "~1.5 h")
        failures: Number of consecutive failed checks
        last_error: Text of the last error
    """
    message = (
        f"⚠️ <b>Провайдер недоступен: {esc(provider_label)}</b>\n\n"
        f"Не отвечает уже {esc(duration_text)} ({failures} проверок подряд).\n\n"
        f"<b>Последняя ошибка:</b> {esc(last_error)}\n\n"
        "Похоже на временные проблемы на стороне провайдера. "
        "Сообщу, когда доступность восстановится."
    )

    await _broadcast_to_admins(
        bot, admin_ids, message, log_label=f"provider outage notification for {provider_label}"
    )


async def send_provider_recovered_notification(
    bot: Bot,
    admin_ids: list[int],
    provider_label: str,
    duration_text: str,
) -> None:
    """
    Send a notification that a provider's availability has recovered.

    Sent only if an alert was previously sent for this provider
    (a sustained outage or a critical error) — to close the "open" incident.

    Delivery failures are logged by _broadcast_to_admins() and not returned.

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        provider_label: Human-readable provider name (display_name)
        duration_text: Outage duration (for example, "~1.5 h")
    """
    message = (
        f"✅ <b>Провайдер восстановлен: {esc(provider_label)}</b>\n\n"
        f"Снова доступен. Был недоступен {esc(duration_text)}."
    )

    await _broadcast_to_admins(
        bot, admin_ids, message, log_label=f"provider recovered notification for {provider_label}"
    )


async def send_server_added_notification(
    bot: Bot,
    admin_ids: list[int],
    server_name: str,
    server_ip: str,
    provider_name: str,
    region: str | None = None,
) -> None:
    """
    Send a notification that a new server has been added.

    Delivery failures are logged by _broadcast_to_admins() and not returned.

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        server_name: Server name
        server_ip: Server IP address
        provider_name: Provider name (vultr, hetzner, etc.)
        region: Server region (optional)
    """
    message = (
        "🟢 <b>Новый сервер обнаружен</b>\n\n"
        f"Обнаружен новый сервер <b>{esc(server_name)}</b> ({esc(server_ip)}) "
        f"у провайдера <b>{esc(provider_name.upper())}</b>.\n"
    )

    if region:
        message += f"\n<b>Регион:</b> {esc(region)}"

    message += "\n\n✅ Мониторинг запущен автоматически."

    await _broadcast_to_admins(bot, admin_ids, message, log_label="server added notification")


async def send_server_removed_notification(
    bot: Bot, admin_ids: list[int], server_name: str, server_ip: str, provider_name: str
) -> None:
    """
    Send a notification that a server has been removed.

    Delivery failures are logged by _broadcast_to_admins() and not returned.

    Args:
        bot: Bot instance
        admin_ids: List of administrator IDs
        server_name: Server name
        server_ip: Server IP address
        provider_name: Provider name (vultr, hetzner, etc.)
    """
    message = (
        "🔴 <b>Сервер удален</b>\n\n"
        f"Сервер <b>{esc(server_name)}</b> ({esc(server_ip)}) больше не существует "
        f"у провайдера <b>{esc(provider_name.upper())}</b>.\n\n"
        "⛔ Мониторинг остановлен.\n"
        "🗑️ Статистика удалена."
    )

    await _broadcast_to_admins(bot, admin_ids, message, log_label="server removed notification")
