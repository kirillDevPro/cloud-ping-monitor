"""Liveness heartbeat registry for supervisor stall detection.

The task supervisor (supervisor.py) restarts a task that CRASHES (its coroutine ends), but
it cannot see a task that is alive yet WEDGED — blocked forever in a thread (asyncio.to_thread),
a never-returning network call, or a deadlocked DB write. Such a stall is the most dangerous
unattended failure: the task is neither done nor progressing, so monitoring silently stops
while everything looks healthy. Each task calls its `heartbeat()` at the top of every loop
iteration; the supervisor compares the age of the last beat against a per-task staleness budget
and alerts the administrators when a task stops making progress.
"""

import logging
import time

logger = logging.getLogger(__name__)


class HeartbeatRegistry:
    """Record the last monotonic time each named task made progress."""

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._last: dict[str, float] = {}

    def beat(self, name: str) -> None:
        """Record that the named task just made progress (one loop iteration).

        Args:
            name: Stable task name (matches the supervised-task key).
        """
        self._last[name] = time.monotonic()

    def seed(self, name: str) -> None:
        """Initialize a task's heartbeat to 'now'.

        Called when a task is (re)started so the supervisor does not flag the brief
        window before its first beat as a stall.

        Args:
            name: Stable task name.
        """
        self._last[name] = time.monotonic()

    def age(self, name: str) -> float | None:
        """Return seconds since the task last beat, or None if it has never beat.

        Args:
            name: Stable task name.

        Returns:
            float | None: Seconds since the last beat, or None when unknown.
        """
        last = self._last.get(name)
        if last is None:
            return None
        return time.monotonic() - last

    def bound_beat(self, name: str):
        """Return a zero-argument callable that records a beat for ``name``.

        Passed into a task as its ``heartbeat`` so the task itself stays decoupled from
        the registry and from its own supervised name.

        Args:
            name: Stable task name to beat for.

        Returns:
            Callable[[], None]: A function that calls ``beat(name)``.
        """

        def _beat() -> None:
            """Record one progress beat for the bound task name."""
            self.beat(name)

        return _beat
