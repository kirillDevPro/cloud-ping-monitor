"""Models for representing a server."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from .provider import ProviderType


class ServerStatus(str, Enum):
    """
    Server availability statuses.

    Inherits from str so the enum serializes correctly to JSON.
    """

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        """Return the status value as a string."""
        return self.value

    def to_emoji(self) -> str:
        """
        Return the emoji that represents this status.

        Returns:
            str: Emoji representation of the status (defaults to "❓" for unknown)
        """
        emoji_map = {
            ServerStatus.ONLINE: "✅",
            ServerStatus.OFFLINE: "❌",
            ServerStatus.UNKNOWN: "❓",
        }
        return emoji_map.get(self, "❓")


class Server(BaseModel):
    """
    Server model.

    A universal model shared across all cloud providers.
    """

    id: str = Field(..., description="Уникальный ID сервера (от провайдера)")

    provider: ProviderType = Field(..., description="Тип облачного провайдера")

    provider_alias: str = Field(
        default="",
        description="Alias экземпляра провайдера (например, 'hetzner_prod')",
    )

    name: str = Field(..., description="Пользовательское имя сервера")

    ip: str = Field(..., description="IP адрес для ping")

    region: str = Field(..., description="Регион размещения сервера")

    plan: str = Field(..., description="Тарифный план сервера")

    status: ServerStatus = Field(
        default=ServerStatus.UNKNOWN, description="Текущий статус доступности"
    )

    last_seen: datetime | None = Field(
        default=None, description="Timestamp последнего успешного ping"
    )

    added_at: datetime = Field(
        default_factory=datetime.now, description="Timestamp добавления в мониторинг"
    )

    enabled: bool = Field(default=True, description="Включен ли мониторинг для этого сервера")

    # Additional fields used for display
    os: str | None = Field(default=None, description="Операционная система")

    ram_mb: int | None = Field(default=None, description="Объём RAM в MB")

    disk_gb: int | None = Field(default=None, description="Объём диска в GB")

    vcpu_count: int | None = Field(default=None, description="Количество vCPU")

    power_status: str | None = Field(
        default=None,
        description="Статус питания сервера от провайдера (например: running, stopped)",
    )

    def __str__(self) -> str:
        """Return a human-readable string representation of the server."""
        return f"{self.name} ({self.provider.value}) - {self.status.value}"

    def get_display_name(self) -> str:
        """
        Return the server name prefixed with its status emoji for display.

        Returns:
            str: Formatted name with the status emoji
        """
        return f"{self.status.to_emoji()} {self.name}"

    @property
    def effective_alias(self) -> str:
        """
        Return the provider instance alias used for identification.

        Uses provider_alias when set; otherwise falls back to provider.value
        (backward compatibility with legacy servers that have no alias).

        Returns:
            str: Provider alias (e.g. "hetzner_prod" or "vultr")
        """
        return self.provider_alias if self.provider_alias else self.provider.value

    @property
    def composite_key(self) -> str:
        """
        Return the composite key that uniquely identifies the server.

        Format: "provider_alias:server_id" (current) or "provider:server_id" (legacy).

        Returns:
            str: Unique server key
        """
        return f"{self.effective_alias}:{self.id}"
