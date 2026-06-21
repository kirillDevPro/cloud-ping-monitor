"""Ping result models for server monitoring."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# Type alias for a server status value (online/offline/unknown)
ServerStatusType = Literal["online", "offline", "unknown"]


class PingStatus(str, Enum):
    """
    Possible outcomes of a ping attempt.

    Inherits from str so the enum serializes correctly to JSON.
    """

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"

    def __str__(self) -> str:
        """Return the underlying string value of the status."""
        return self.value


class PingResult(BaseModel):
    """
    Result of a single ping attempt against a server.

    Used to store the detailed monitoring history.
    """

    server_id: str = Field(..., description="ID сервера")

    provider_type: str = Field(..., description="Тип провайдера (vultr, hetzner и т.д.)")

    timestamp: datetime = Field(default_factory=datetime.now, description="Время выполнения пинга")

    status: PingStatus = Field(..., description="Статус результата пинга")

    response_time_ms: float | None = Field(
        default=None,
        description="Время отклика в миллисекундах (только для SUCCESS)",
        ge=0.0,
    )

    error: str | None = Field(default=None, description="Описание ошибки (для FAILED/TIMEOUT)")

    packet_loss: float = Field(
        default=0.0, description="Потеря пакетов в процентах", ge=0.0, le=100.0
    )

    consecutive_failures: int = Field(
        default=0, description="Количество последовательных неудачных пингов", ge=0
    )

    current_status: ServerStatusType = Field(
        default="unknown", description="Текущий статус сервера (online/unknown/offline)"
    )

    previous_status: ServerStatusType = Field(
        default="unknown",
        description="Предыдущий статус сервера (online/unknown/offline)",
    )

    def __str__(self) -> str:
        """Return a short human-readable representation of the ping result."""
        if self.status == PingStatus.SUCCESS:
            return f"SUCCESS ({self.response_time_ms:.2f}ms)"
        elif self.status == PingStatus.TIMEOUT:
            return "TIMEOUT"
        else:
            return f"FAILED: {self.error}"

    def is_successful(self) -> bool:
        """
        Check whether the ping succeeded.

        Returns:
            bool: True if the ping was successful, otherwise False.
        """
        return self.status == PingStatus.SUCCESS

    def is_slow(self, threshold_ms: float = 300.0) -> bool:
        """
        Check whether the ping is considered slow.

        Args:
            threshold_ms: Response-time threshold in milliseconds (default 300ms).

        Returns:
            bool: True if a successful ping is slower than the threshold, otherwise False.
        """
        if not self.is_successful() or self.response_time_ms is None:
            return False

        return self.response_time_ms > threshold_ms

    def get_display_text(self) -> str:
        """
        Build the result text shown in Telegram.

        Returns:
            str: Formatted result text with a status emoji.
        """
        if self.status == PingStatus.SUCCESS:
            emoji = "🟢" if not self.is_slow() else "🟡"
            return f"{emoji} {self.response_time_ms:.2f}ms"
        elif self.status == PingStatus.TIMEOUT:
            return "🔴 Timeout"
        else:
            return f"🔴 Error: {self.error}"


class PingStatistics(BaseModel):
    """
    Aggregated ping statistics for a server.

    Used to display overall monitoring metrics.
    """

    server_id: str = Field(..., description="ID сервера")

    total_pings: int = Field(default=0, description="Общее количество пингов", ge=0)

    successful_pings: int = Field(default=0, description="Количество успешных пингов", ge=0)

    failed_pings: int = Field(default=0, description="Количество неудачных пингов", ge=0)

    timeout_pings: int = Field(default=0, description="Количество пингов с timeout", ge=0)

    avg_response_time_ms: float = Field(
        default=0.0, description="Средний response time в мс", ge=0.0
    )

    min_response_time_ms: float | None = Field(
        default=None, description="Минимальный response time в мс", ge=0.0
    )

    max_response_time_ms: float | None = Field(
        default=None, description="Максимальный response time в мс", ge=0.0
    )

    uptime_percentage: float = Field(default=100.0, description="Процент uptime", ge=0.0, le=100.0)

    last_downtime: datetime | None = Field(default=None, description="Timestamp последнего сбоя")

    def get_display_text(self) -> str:
        """
        Build the statistics text for display (compact format).

        Returns:
            str: Formatted statistics (HTML formatting).
        """
        lines = []

        # First line: ping counts and uptime
        # Format: 📊 95/100 ✓ • 3🔴 2⏱️ • ⬆95%
        line1 = f"📊 {self.successful_pings}/{self.total_pings} ✓"

        if self.failed_pings > 0 or self.timeout_pings > 0:
            errors = []
            if self.failed_pings > 0:
                errors.append(f"{self.failed_pings}🔴")
            if self.timeout_pings > 0:
                errors.append(f"{self.timeout_pings}⏱️")
            line1 += f" • {' '.join(errors)}"

        line1 += f" • ⬆{self.uptime_percentage:.0f}%"
        lines.append(line1)

        # Second line: ping latency stats (only when there are successful pings)
        # Format: ⚡ ~150ms (100↓ 250↑)
        if self.successful_pings > 0:
            line2 = f"⚡ ~{self.avg_response_time_ms:.0f}ms"

            if self.min_response_time_ms is not None and self.max_response_time_ms is not None:
                line2 += f" ({self.min_response_time_ms:.0f}↓ {self.max_response_time_ms:.0f}↑)"

            lines.append(line2)

        return "\n".join(lines)
