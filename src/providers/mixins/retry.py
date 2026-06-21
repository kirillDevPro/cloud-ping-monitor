"""Mixin providing retry logic with exponential backoff.

Eliminates the ~150 lines of duplicated retry code present in every provider.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class RetryConfig:
    """Retry configuration.

    Attributes:
        max_retries: Maximum number of attempts before giving up.
        initial_delay: Delay in seconds before the first backoff wait.
        max_delay: Upper bound (in seconds) for the exponential backoff delay.
        exponential_base: Multiplier applied to the delay after each attempt.
        jitter: Whether to apply full jitter to the backoff delay.
    """

    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True


class RetryMixin:
    """
    Mixin providing retry logic for providers.

    Providers should override the following methods:
    - _should_retry() - decides whether a request should be retried
    - _transform_error() - converts an error into a provider-specific exception

    Usage example:
        class VultrProvider(BaseProvider, RetryMixin):
            def _should_retry(self, error, attempt):
                if isinstance(error, httpx.HTTPStatusError):
                    if error.response.status_code == 429:
                        retry_after = error.response.headers.get("Retry-After", "5")
                        return True, float(retry_after)
                    if error.response.status_code >= 500:
                        return True, 0.0
                return False, 0.0

            async def get_servers(self):
                return await self._retry_with_backoff(self._fetch_servers)
    """

    def _should_retry(
        self, error: Exception, attempt: int
    ) -> tuple[bool, float]:
        """
        Decide whether a request should be retried.

        Providers MUST override this method with their own logic.

        Args:
            error: The exception that was raised.
            attempt: The attempt number (0-based).

        Returns:
            tuple[bool, float]: (should_retry, custom_wait_time)
                - should_retry: True if the request should be retried.
                - custom_wait_time: Wait time in seconds (0 = use exponential backoff).
        """
        return False, 0.0

    def _transform_error(self, error: Exception) -> Exception:
        """
        Convert an error into a provider-specific exception.

        Providers may override this method to translate httpx/boto3 errors
        into their own specific exceptions.

        Args:
            error: The original exception.

        Returns:
            Exception: The transformed exception.
        """
        return error

    async def _retry_with_backoff(
        self,
        func: Callable[[], Awaitable[T]],
        config: RetryConfig | None = None,
    ) -> T:
        """
        Execute an async function with retry logic.

        Args:
            func: The async function to execute (takes no arguments; use a lambda).
            config: Retry configuration (optional; defaults to RetryConfig()).

        Returns:
            T: The result of the function call.

        Raises:
            Exception: The last exception (transformed via _transform_error) after
                all attempts are exhausted.

        Example:
            result = await self._retry_with_backoff(
                lambda: self._fetch_data(server_id),
                config=RetryConfig(max_retries=5)
            )
        """
        cfg = config or RetryConfig()
        delay = cfg.initial_delay
        last_error: Exception | None = None

        for attempt in range(cfg.max_retries):
            try:
                return await func()
            except Exception as e:
                last_error = e

                should_retry, custom_delay = self._should_retry(e, attempt)

                # Do not retry on the last attempt or when retry is not needed
                if not should_retry or attempt >= cfg.max_retries - 1:
                    raise self._transform_error(e) from e

                # Compute the wait time
                if custom_delay > 0:
                    wait_time = custom_delay
                else:
                    wait_time = delay
                    if cfg.jitter:
                        # Full jitter to prevent the thundering herd problem
                        wait_time = random.uniform(0, wait_time)

                logger.warning(
                    f"Retry attempt {attempt + 1}/{cfg.max_retries}, "
                    f"waiting {wait_time:.1f}s: {type(e).__name__}: {e}"
                )

                await asyncio.sleep(wait_time)

                # Exponential backoff for the next attempt
                delay = min(delay * cfg.exponential_base, cfg.max_delay)

        # Should never reach here, but kept as a safeguard
        if last_error:
            raise self._transform_error(last_error) from last_error
        raise RuntimeError("Unexpected error in retry logic")
