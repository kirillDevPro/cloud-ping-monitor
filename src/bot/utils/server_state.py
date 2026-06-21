"""Synchronization of server statuses from the monitoring shared_state.

Used by the bot routers (monitoring, servers) to project the current status
of the worker processes (the shared_state DictProxy) onto the Server models
before rendering. Previously this loop was copied ~9 times across the routers.
"""

from multiprocessing.managers import DictProxy

from ...models import Server, ServerStatus

# Mapping from the string status in shared_state to the ServerStatus enum
_STATUS_MAP = {
    "online": ServerStatus.ONLINE,
    "offline": ServerStatus.OFFLINE,
}


def apply_shared_status(servers: list[Server], shared_state: DictProxy) -> None:
    """Set ``server.status`` from shared_state, keyed by composite_key.

    An unknown or missing status is treated as ``ServerStatus.UNKNOWN``.
    Mutates the passed Server objects in place.

    Args:
        servers: Servers whose status needs to be updated
        shared_state: Shared state of the worker processes
    """
    for server in servers:
        state = shared_state.get(server.composite_key, {})
        server.status = _STATUS_MAP.get(state.get("status"), ServerStatus.UNKNOWN)
