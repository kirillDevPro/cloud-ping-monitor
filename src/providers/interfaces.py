"""Interfaces for cloud service providers.

Protocol classes are used for structural typing (duck typing).
This allows checking type compatibility without explicit inheritance.
"""

from typing import Protocol, runtime_checkable

from ..models import Server
from ..models.billing import BillingModel
from ..storage.balance import BalanceRecord


@runtime_checkable
class IServerReader(Protocol):
    """Interface for reading server information."""

    async def get_servers(self) -> list[Server]:
        """
        Retrieve the list of all servers from the provider API.

        Returns:
            list[Server]: List of all servers
        """
        ...

    async def get_server(self, server_id: str) -> Server | None:
        """
        Retrieve information about a specific server.

        Args:
            server_id: Server ID

        Returns:
            Server | None: Server object, or None if not found
        """
        ...


@runtime_checkable
class IServerController(Protocol):
    """Interface for managing servers (start/stop/reboot)."""

    async def start_server(self, server_id: str) -> bool:
        """
        Start a stopped server.

        Args:
            server_id: Server ID

        Returns:
            bool: True if the operation succeeded
        """
        ...

    async def stop_server(self, server_id: str) -> bool:
        """
        Stop a running server (hard stop).

        Args:
            server_id: Server ID

        Returns:
            bool: True if the operation succeeded
        """
        ...

    async def reboot_server(self, server_id: str) -> bool:
        """
        Reboot a server.

        Args:
            server_id: Server ID

        Returns:
            bool: True if the operation succeeded
        """
        ...


@runtime_checkable
class IGracefulShutdown(Protocol):
    """Optional interface for gracefully shutting down a server."""

    async def shutdown_server(self, server_id: str) -> bool:
        """
        Gracefully shut down a server.

        Sends an ACPI shutdown signal, allowing the OS to terminate cleanly.

        Args:
            server_id: Server ID

        Returns:
            bool: True if the operation succeeded
        """
        ...

    def supports_graceful_shutdown(self, server_id: str | None = None) -> bool:
        """
        Check whether graceful shutdown is supported (provider-level or per-server).

        Args:
            server_id: If provided, checks support for a specific server; if None,
                returns the provider-level capability flag

        Returns:
            bool: True if graceful shutdown is supported
        """
        ...


@runtime_checkable
class IBalanceProvider(Protocol):
    """Interface for retrieving the account balance."""

    async def get_balance(self) -> BalanceRecord | None:
        """
        Retrieve account balance information.

        Returns:
            BalanceRecord | None: Balance information, or None if unavailable
        """
        ...

    def supports_balance(self) -> bool:
        """
        Check whether the balance API is supported.

        Returns:
            bool: True if the provider supports retrieving the balance
        """
        ...


@runtime_checkable
class IBillingModel(Protocol):
    """Interface for determining the provider's billing model."""

    def get_billing_model(self) -> BillingModel:
        """
        Return the provider's billing model.

        Returns:
            BillingModel: PREPAID or POSTPAID
        """
        ...

    def should_save_balance_history(self) -> bool:
        """
        Determine whether balance history should be saved.

        Returns:
            bool: True if history should be saved
        """
        ...

    def should_check_balance_threshold(self) -> bool:
        """
        Determine whether the balance threshold should be checked for notifications.

        Returns:
            bool: True if the threshold should be checked
        """
        ...


@runtime_checkable
class IProviderLifecycle(Protocol):
    """Interface for the provider lifecycle."""

    async def close(self) -> None:
        """Close the provider's resources (HTTP clients, etc.)."""
        ...

    async def health_check(self) -> bool:
        """
        Check whether the provider API is reachable.

        Returns:
            bool: True if the API is reachable
        """
        ...


@runtime_checkable
class IProviderMetadata(Protocol):
    """Interface for provider metadata used in the UI."""

    def get_provider_name(self) -> str:
        """
        Return the provider's technical name.

        Returns:
            str: Provider name (e.g., "Vultr")
        """
        ...

    def get_provider_display_name(self) -> str:
        """
        Return the display name for the UI.

        Returns:
            str: Provider display name
        """
        ...

    def get_provider_emoji(self) -> str:
        """
        Return the provider's emoji for the UI.

        Returns:
            str: Provider emoji
        """
        ...


# Composite type for a full-featured provider
class IFullProvider(
    IServerReader,
    IServerController,
    IBalanceProvider,
    IBillingModel,
    IProviderLifecycle,
    IProviderMetadata,
    Protocol,
):
    """Full-featured provider implementing all interfaces."""

    pass
