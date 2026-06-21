"""Abstract base class for cloud providers."""

import logging
from abc import ABC, abstractmethod

from ..models import Server
from ..models.billing import BillingModel
from ..storage.balance import BalanceRecord

logger = logging.getLogger(__name__)


class BaseProvider(ABC):
    """
    Abstract base class for all cloud providers.

    Defines a unified interface for working with different cloud
    platforms (Vultr, AWS, DigitalOcean, Azure).

    Every concrete provider must implement these methods.

    Attributes:
        _alias: Unique identifier of the provider instance
        _display_name: Display name of the provider
        _emoji: ASCII emoji for the UI
    """

    def __init__(
        self,
        alias: str = "",
        display_name: str = "",
        emoji: str = "",
    ) -> None:
        """
        Initialize the base provider.

        Args:
            alias: Unique identifier of the instance (e.g. "hetzner_prod")
            display_name: Display name for the UI
            emoji: ASCII emoji for the UI
        """
        self._alias = alias
        self._display_name = display_name
        self._emoji = emoji

    @property
    def alias(self) -> str:
        """Return the alias of the provider instance."""
        return self._alias

    @abstractmethod
    async def get_servers(self) -> list[Server]:
        """
        Fetch the list of all servers from the provider API.

        IMPORTANT: Must handle pagination correctly!
        Many APIs return at most 100 items per page.

        Returns:
            List[Server]: List of all servers

        Raises:
            Exception: On API errors
        """
        pass

    @abstractmethod
    async def get_server(self, server_id: str) -> Server | None:
        """
        Fetch information about a specific server.

        Args:
            server_id: Server ID

        Returns:
            Optional[Server]: The server object, or None if not found

        Raises:
            Exception: On API errors
        """
        pass

    @abstractmethod
    async def start_server(self, server_id: str) -> bool:
        """
        Start a stopped server.

        Args:
            server_id: Server ID

        Returns:
            bool: True if the operation succeeded, False otherwise

        Raises:
            Exception: On API errors
        """
        pass

    @abstractmethod
    async def stop_server(self, server_id: str) -> bool:
        """
        Stop a running server (hard stop).

        Args:
            server_id: Server ID

        Returns:
            bool: True if the operation succeeded, False otherwise

        Raises:
            Exception: On API errors
        """
        pass

    @abstractmethod
    async def shutdown_server(self, server_id: str) -> bool:
        """
        Gracefully shut down a server (graceful shutdown).

        Sends an ACPI shutdown signal, allowing the OS to shut down cleanly.
        Some providers may not support this operation.

        Args:
            server_id: Server ID

        Returns:
            bool: True if the operation succeeded, False otherwise

        Raises:
            Exception: On API errors
        """
        pass

    @abstractmethod
    async def reboot_server(self, server_id: str) -> bool:
        """
        Reboot a server.

        Args:
            server_id: Server ID

        Returns:
            bool: True if the operation succeeded, False otherwise

        Raises:
            Exception: On API errors
        """
        pass

    @abstractmethod
    async def get_balance(self) -> BalanceRecord | None:
        """
        Fetch account balance information.

        IMPORTANT: Some providers return the balance with a negative sign!
        The value must be normalized before being returned.

        IMPORTANT: Some providers (Hetzner) do NOT expose a balance via the API.
        In that case None is returned. Check supports_balance() before calling.

        Returns:
            Optional[BalanceRecord]: Balance information, or None if unavailable

        Raises:
            Exception: On API errors
        """
        pass

    async def close(self) -> None:
        """
        Close the HTTP client and other provider resources.

        The base implementation does nothing.
        Providers with HTTP clients must override this method.
        """
        pass

    def supports_balance(self) -> bool:
        """
        Check whether the provider supports retrieving the balance via the API.

        The base implementation returns True.
        Providers without a balance API must override it to return False.

        Returns:
            bool: True if the provider supports balance, False otherwise
        """
        return True

    def supports_graceful_shutdown(self, server_id: str | None = None) -> bool:
        """
        Check whether the provider/a specific server supports graceful shutdown (ACPI).

        Graceful shutdown differs from a hard stop (stop_server): the OS
        receives a signal to shut down cleanly. Not all providers expose a
        dedicated endpoint (for example, Vultr uses halt, which is equivalent
        to a hard stop).

        The base implementation returns False (opt-in). Providers with real
        support override it to return True. Used by the bot to show the
        graceful shutdown button only where it is meaningful.

        Args:
            server_id: If provided, checks for a specific server (some
                providers, such as AWS, support graceful shutdown only for part
                of their instances). If None, returns the provider-level capability.

        Returns:
            bool: True if graceful shutdown is supported
        """
        return False

    def get_billing_model(self) -> BillingModel:
        """
        Return the provider's billing model.

        Prepaid model (PREPAID):
        - The user tops up the balance in advance
        - balance = remaining funds in the account
        - pending_charges = upcoming charges
        - It makes sense to track balance history and check thresholds

        Postpaid model (POSTPAID):
        - Billing at the end of the month based on actual usage
        - monthly_costs = costs accrued for the month
        - There is no point in checking thresholds (no concept of a "balance")

        The base implementation returns PREPAID.
        Postpaid providers (AWS) must override it.

        Returns:
            BillingModel: PREPAID or POSTPAID
        """
        return BillingModel.PREPAID

    def should_save_balance_history(self) -> bool:
        """
        Determine whether balance history should be saved.

        For prepaid - yes (tracking the balance spending trend)
        For postpaid - yes (tracking the cost trend)

        By default, save history for all providers with a balance API.

        Returns:
            bool: True if history should be saved
        """
        return self.supports_balance()

    def should_check_balance_threshold(self) -> bool:
        """
        Determine whether the balance threshold should be checked for notifications.

        Low-balance notifications only make sense for prepaid providers,
        where the balance can run out and lead to services being stopped.

        For postpaid providers (AWS) the threshold is not checked,
        since billing happens after the fact.

        Returns:
            bool: True if the threshold should be checked and notifications sent
        """
        return self.get_billing_model() == BillingModel.PREPAID and self.supports_balance()

    async def health_check(self) -> bool:
        """
        Check whether the provider API is reachable.

        The base implementation attempts to fetch the list of servers.
        May be overridden with lighter-weight checks.

        Returns:
            bool: True if the API is reachable, False otherwise
        """
        try:
            await self.get_servers()
            return True
        except Exception as e:
            logger.debug(f"Provider accessibility check failed: {e}")
            return False

    def get_provider_name(self) -> str:
        """
        Return a human-readable provider name.

        Returns:
            str: Provider name
        """
        return self.__class__.__name__.replace("Provider", "")

    def get_provider_display_name(self) -> str:
        """
        Return the display name of the provider for the UI.

        If _display_name is set, return it.
        Otherwise return the plain class name.

        Returns:
            str: Provider display name (e.g. "Hetzner (prod)")
        """
        if self._display_name:
            return self._display_name
        return self.get_provider_name()

    def get_provider_emoji(self) -> str:
        """
        Return the provider emoji for the UI.

        If _emoji is set, return it.
        Otherwise return a neutral emoji.

        Returns:
            str: Provider emoji
        """
        if self._emoji:
            return self._emoji
        return "[?]"
