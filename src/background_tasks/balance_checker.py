"""Background task for checking account balances at cloud providers."""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from aiogram import Bot

if TYPE_CHECKING:
    from ..storage import BalanceRepository
    from ..providers.manager import ProviderManager

from ..bot.notifications import send_low_balance_notification
from ..storage.balance import PrepaidBalanceRecord

logger = logging.getLogger(__name__)


async def balance_checker(
    bot: Bot,
    balance_repo: "BalanceRepository",  # type: ignore  # noqa: F821
    provider_manager: "ProviderManager",  # type: ignore  # noqa: F821
    admin_ids: list[int],
    check_interval: int,
    threshold: float,
    heartbeat: Callable[[], None] = lambda: None,
) -> None:
    """
    Background task that periodically checks account balances.

    The function runs in an infinite loop and:
    1. Fetches the current balance from every provider that supports get_balance()
    2. Saves a record to history (if should_save_balance_history() == True)
    3. Checks the threshold and sends notifications (if should_check_balance_threshold() == True)

    Polymorphism:
    - should_save_balance_history() decides whether history should be saved
    - should_check_balance_threshold() decides whether the threshold should be checked
    - display_value - unified value used both for display and for the threshold check

    Args:
        bot: aiogram Bot instance used to send messages
        balance_repo: Balance history repository
        provider_manager: Manager of all cloud providers
        admin_ids: List of administrator IDs to notify
        check_interval: Check interval in seconds
        threshold: Notification threshold in USD (applied only to prepaid providers)
        heartbeat: Called once per loop iteration so the supervisor can detect a stall.
            Defaults to a no-op for standalone use/tests.

    Raises:
        asyncio.CancelledError: Re-raised when the task is cancelled.
        Exception: Re-raised on an unrecoverable error outside a check cycle.
    """
    try:
        while True:
            heartbeat()  # progress beat at the top of every loop iteration
            # Wait until the next check (at the start of the loop, since the initial check is already done in main.py)
            await asyncio.sleep(check_interval)

            try:
                # Check the balance at every provider
                # get_all_providers() returns dict[str, tuple[BaseProvider, ProviderConfig]]
                for alias, (provider, config) in provider_manager.get_all_providers().items():
                    # Skip providers without a balance API (e.g. Hetzner)
                    if not provider.supports_balance():
                        continue

                    try:
                        # Fetch the balance data
                        balance_record = await provider.get_balance()

                        if balance_record is None:
                            continue

                        # Set provider_alias on the record (if not already set)
                        if not balance_record.provider_alias:
                            balance_record.provider_alias = alias

                        # Save to history (polymorphic call)
                        # Offload blocking JSON I/O so it doesn't stall the event loop.
                        if provider.should_save_balance_history():
                            await asyncio.to_thread(balance_repo.add_record, balance_record)

                        # Check the balance threshold (polymorphic call)
                        # Notifications are sent only for prepaid providers
                        if provider.should_check_balance_threshold():
                            # display_value returns effective_balance for prepaid
                            current_value = balance_record.display_value
                            if current_value < threshold:
                                # Detailed logging only for prepaid
                                if isinstance(balance_record, PrepaidBalanceRecord):
                                    logger.warning(
                                        f"Low balance detected for {alias}: "
                                        f"${current_value:.2f} < ${threshold:.2f} "
                                        f"(balance=${balance_record.balance:.2f}, "
                                        f"pending=${balance_record.pending_charges:.2f})"
                                    )
                                else:
                                    logger.warning(
                                        f"Low balance detected for {alias}: "
                                        f"${current_value:.2f} < ${threshold:.2f}"
                                    )

                                days_left = await asyncio.to_thread(
                                    balance_repo.estimate_days_until_empty,
                                    provider_alias=alias,
                                )
                                await send_low_balance_notification(
                                    bot=bot,
                                    admin_ids=admin_ids,
                                    balance=current_value,
                                    threshold=threshold,
                                    days_left=days_left,
                                    provider_name=provider.get_provider_display_name(),
                                )

                    except Exception as e:
                        logger.error(
                            f"Error checking balance for {alias}: {e}",
                            exc_info=True,
                        )

                # Clean up old data (older than 90 days)
                deleted_count = await asyncio.to_thread(balance_repo.cleanup_old_data, days=90)
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old balance records")

            except Exception as e:
                logger.error(f"Error in balance checker cycle: {e}", exc_info=True)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Critical error in balance checker: {e}", exc_info=True)
        raise
