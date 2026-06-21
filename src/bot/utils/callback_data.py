"""Utilities for working with callback_data in the Telegram bot.

Telegram limits callback_data length to 64 bytes.
For AWS servers the composite key can be long: aws:region:instance_id

This module provides functions for shortening long callback_data.

Thread-safety: A threading.Lock guards the cache against concurrent access
from different callback handlers.

Persistence: The cache is stored in SQLite so it survives bot restarts.
"""

import hashlib
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum size of the in-memory cache (LRU eviction)
MAX_CACHE_SIZE = 1000

# Call counter for periodic cleanup
_encode_call_counter = 0
CLEANUP_INTERVAL = 1000  # Run cleanup every 1000 encode calls

# Maximum rows kept in the SQLite cache. created_at is refreshed on every read,
# so the cap evicts genuinely least-recently-used hashes, never live buttons.
MAX_DB_ROWS = 5000

# Lock for thread-safe access to the cache
_cache_lock = threading.Lock()

# Global cache mapping hash -> full_server_key
# OrderedDict is used for LRU eviction
_callback_cache: OrderedDict[str, str] = OrderedDict()

# Path to the SQLite database for persistent storage. Anchored on the project
# root (never CWD-relative); init_callback_cache() re-points it at the configured
# DATA_DIR during app startup so it lives next to the other data files.
DB_PATH = Path(__file__).resolve().parents[3] / "data" / "callback_cache.db"


def _init_db() -> None:
    """
    Initialize the SQLite database for persistent cache storage.

    Creates the table if it does not exist.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS callback_cache (
                hash TEXT PRIMARY KEY,
                server_key TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        # Index for cleaning up old records
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_callback_cache_created_at
            ON callback_cache(created_at)
        """)


def _save_to_db(server_hash: str, server_key: str) -> None:
    """
    Save the hash -> server_key mapping to SQLite.

    Args:
        server_hash: Hash of the server_key
        server_key: Full server key
    """
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO callback_cache (hash, server_key, created_at) VALUES (?, ?, ?)",
                (server_hash, server_key, int(time.time())),
            )
    except sqlite3.Error as e:
        logger.error(f"Failed to save callback hash to DB: {e}")


def _load_from_db(server_hash: str) -> str | None:
    """
    Load a server_key from SQLite by its hash.

    Args:
        server_hash: Hash to look up

    Returns:
        The server_key, or None if not found
    """
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            row = conn.execute(
                "SELECT server_key FROM callback_cache WHERE hash = ?", (server_hash,)
            ).fetchone()
            if not row:
                return None
            # Refresh recency so an actively-used (but old) button is never evicted
            # by the row-cap cleanup — turns created_at into a last-used timestamp.
            conn.execute(
                "UPDATE callback_cache SET created_at = ? WHERE hash = ?",
                (int(time.time()), server_hash),
            )
            return row[0]
    except sqlite3.Error as e:
        logger.error(f"Failed to load callback hash from DB: {e}")
        return None


def _cleanup_excess_entries(max_rows: int = MAX_DB_ROWS) -> None:
    """
    Trim the cache to its newest ``max_rows`` rows (by last-used created_at).

    Replaces age-based expiry: Telegram keeps inline buttons in chat history
    indefinitely, so deleting a hash purely because it is old breaks still-live
    buttons. created_at is refreshed on every read, so this row cap evicts only
    genuinely least-recently-used mappings — and only once the table grows past
    max_rows, which a bounded server fleet never reaches in practice.

    Args:
        max_rows: Maximum number of mappings to retain.
    """
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute(
                """
                DELETE FROM callback_cache
                WHERE hash IN (
                    SELECT hash FROM callback_cache
                    ORDER BY created_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (max_rows,),
            )
    except sqlite3.Error as e:
        logger.error(f"Failed to cleanup excess callback hashes: {e}")


def init_callback_cache(data_dir: Path | None = None) -> None:
    """
    Point the cache DB at the configured data dir and (re)initialize it.

    Called once during app startup with settings.DATA_DIR so the callback cache
    lives next to the other data files regardless of the process working directory.

    Args:
        data_dir: Directory holding the data files. When None, keeps the current
            DB_PATH (the project-root default).
    """
    global DB_PATH
    if data_dir is not None:
        DB_PATH = Path(data_dir) / "callback_cache.db"
    _init_db()
    _cleanup_excess_entries()


# Initialize the database at the default (project-root) path on import so the
# module is usable before init_callback_cache() runs (e.g. in tests).
_init_db()


def _hash_server_key(server_key: str) -> str:
    """
    Create a short hash for a long server_key.

    Args:
        server_key: Full server key (e.g. "aws:us-east-1:i-xxx")

    Returns:
        Short hash (first 12 characters of the SHA256 digest)
    """
    return hashlib.sha256(server_key.encode()).hexdigest()[:12]


def encode_callback_data(prefix: str, server_key: str, max_length: int = 64) -> str:
    """
    Encode callback_data, shortening the server_key when necessary.

    Args:
        prefix: Callback prefix (e.g. "server_control_")
        server_key: Server key (provider:server_id or provider:region:server_id)
        max_length: Maximum callback_data length (default: 64 for Telegram)

    Returns:
        Encoded callback_data
    """
    global _encode_call_counter

    # Periodic cleanup (prevents the database from growing unbounded)
    _encode_call_counter += 1
    if _encode_call_counter >= CLEANUP_INTERVAL:
        _encode_call_counter = 0
        try:
            _cleanup_excess_entries()
        except Exception as e:
            logger.debug(f"Cleanup error: {e}")

    full_callback = f"{prefix}{server_key}"

    # If it fits, return it as is
    if len(full_callback) <= max_length:
        return full_callback

    # Otherwise shorten the server_key via a hash
    server_hash = _hash_server_key(server_key)

    # Thread-safe insertion into the cache with LRU eviction
    with _cache_lock:
        _callback_cache[server_hash] = server_key
        _callback_cache.move_to_end(server_hash)  # LRU: move to the end

        # Cap the cache size (evict the oldest entries)
        while len(_callback_cache) > MAX_CACHE_SIZE:
            _callback_cache.popitem(last=False)

    # Persist to SQLite
    _save_to_db(server_hash, server_key)

    short_callback = f"{prefix}#{server_hash}"

    return short_callback


def decode_callback_data(callback_data: str, prefix: str = "") -> str | None:
    """
    Decode callback_data, restoring the full server_key when necessary.

    Args:
        callback_data: Callback data string
        prefix: Prefix to strip (optional)

    Returns:
        The full server_key, or None on error
    """
    try:
        # Strip the prefix
        server_key = callback_data.replace(prefix, "") if prefix else callback_data

        # Check whether this is a hash (starts with #)
        if server_key.startswith("#"):
            server_hash = server_key[1:]  # Drop the leading #

            # Thread-safe lookup in the in-memory cache
            with _cache_lock:
                if server_hash in _callback_cache:
                    # LRU: move to the end on access
                    _callback_cache.move_to_end(server_hash)
                    return _callback_cache[server_hash]

            # Fallback: look it up in SQLite (e.g. after a restart)
            db_result = _load_from_db(server_hash)
            if db_result:
                # Add to the in-memory cache to speed up subsequent lookups
                with _cache_lock:
                    _callback_cache[server_hash] = db_result
                    _callback_cache.move_to_end(server_hash)
                    # Cap the cache size
                    while len(_callback_cache) > MAX_CACHE_SIZE:
                        _callback_cache.popitem(last=False)
                return db_result

            logger.error(f"Hash not found in cache or DB: #{server_hash}")
            return None

        # Plain server_key (not a hash)
        return server_key

    except Exception as e:
        logger.error(f"Failed to decode callback_data '{callback_data}': {e}")
        return None
