"""Singleton per-user language preference store.

Mirrors the callback-data cache pattern in ``src/bot/utils/callback_data.py``: a
module-level store with an import-time default path (so it is usable before app
startup, e.g. in tests) that :func:`init_language_store` re-points at the
configured ``DATA_DIR`` during startup. Preferences persist to
``data/user_preferences.json`` (``{str(user_id): language}``) with an atomic
write so a crash mid-save cannot corrupt the file.

Exposing free functions (rather than a DI-injected repository) lets BOTH the
language middleware AND the background-task notification broadcaster resolve a
user's language without threading a repository through every call site —
notifications are rendered per recipient and need the lookup from code that has
no access to the aiogram DI container.

Access is single-process (handlers and background tasks run in the bot's main
process; ping workers never touch user state), so a single lock guarding the
in-memory dict and the file write is sufficient.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from .translator import DEFAULT_LANGUAGE, normalize_language

logger = logging.getLogger(__name__)

# Guards the in-memory cache (brief, no I/O — readers only ever take this lock).
_lock = threading.Lock()

# Serializes writers so a slower write cannot land its os.replace after a newer
# write's, leaving the file with a stale snapshot. Held across a single writer's
# whole update+persist; readers never take it, so the file I/O it covers never
# blocks a get_user_language reader.
_write_lock = threading.Lock()

# In-memory cache: str(user_id) -> language code.
_languages: dict[str, str] = {}

# Path to the persisted preferences. Anchored on the project root (never
# CWD-relative); init_language_store() re-points it at the configured DATA_DIR
# during startup so it lives next to the other data files.
_FILE_PATH: Path = Path(__file__).resolve().parents[3] / "data" / "user_preferences.json"

# Whether the on-disk file has been loaded into the cache yet.
_loaded: bool = False


def _read_file() -> dict[str, str]:
    """Read and validate the preferences file.

    Returns:
        dict[str, str]: Mapping of ``str(user_id)`` to a normalized language
            code. Returns an empty mapping when the file is missing, empty, or
            malformed (the file is left untouched for manual inspection).
    """
    if not _FILE_PATH.exists():
        return {}
    try:
        with open(_FILE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read user preferences from %s: %s", _FILE_PATH, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("Unexpected user-preferences format in %s: %s", _FILE_PATH, type(raw))
        return {}

    # Keep only well-formed, supported entries; normalize stored languages.
    result: dict[str, str] = {}
    for user_id, language in raw.items():
        if isinstance(user_id, str) and isinstance(language, str):
            result[user_id] = normalize_language(language)
    return result


def _persist(data: dict[str, str]) -> bool:
    """Atomically write a preferences snapshot to disk.

    Uses a write-to-temp-then-rename pattern (``os.replace`` is atomic on Windows
    and Unix) so a partial write can never corrupt the live file. Takes an explicit
    snapshot (captured under ``_lock`` by the caller) and is itself LOCK-FREE, so the
    potentially slow disk write never blocks concurrent ``get_user_language`` readers.

    Args:
        data: Snapshot of the ``str(user_id) -> language`` mapping to write.

    Returns:
        bool: True if the snapshot was durably written to disk; False if the write
            failed (the error is logged; the in-memory cache is unaffected here).
    """
    temp_path: str | None = None
    try:
        _FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=_FILE_PATH.parent, prefix=f".{_FILE_PATH.stem}_", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, _FILE_PATH)
        temp_path = None
        return True
    except OSError as exc:
        logger.error("Failed to persist user preferences to %s: %s", _FILE_PATH, exc)
        return False
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def init_language_store(data_dir: Path | None = None) -> None:
    """Point the store at the configured data dir and load it into memory.

    Called once during app startup with ``settings.DATA_DIR`` so preferences live
    next to the other data files regardless of the process working directory.

    Args:
        data_dir: Directory holding the data files. When None, keeps the current
            path (the project-root default).

    Returns:
        None.
    """
    global _FILE_PATH, _languages, _loaded
    with _lock:
        if data_dir is not None:
            _FILE_PATH = Path(data_dir) / "user_preferences.json"
        _languages = _read_file()
        _loaded = True
    logger.info("Language store initialized at %s (%d stored)", _FILE_PATH, len(_languages))


def _ensure_loaded() -> None:
    """Lazily load the file on first access if init was never called.

    Keeps the module usable in tests that call the accessors without running
    startup. Caller MUST hold ``_lock``.

    Returns:
        None.
    """
    global _languages, _loaded
    if not _loaded:
        # init_language_store() should run at startup; reaching here means it did
        # not (a test or a startup-ordering bug). Warn loudly rather than silently
        # doing a blocking file read, but still load so behavior stays correct.
        logger.warning(
            "Language store accessed before init_language_store(); lazy-loading %s",
            _FILE_PATH,
        )
        _languages = _read_file()
        _loaded = True


def get_user_language(user_id: int) -> str:
    """Return the stored language for a user, or the default when unset.

    Args:
        user_id: Telegram user id.

    Returns:
        str: The user's stored language code, or :data:`DEFAULT_LANGUAGE` when
            the user has never picked one.
    """
    with _lock:
        _ensure_loaded()
        return _languages.get(str(user_id), DEFAULT_LANGUAGE)


def set_user_language(user_id: int, language: str) -> bool:
    """Persist a user's language choice.

    The in-memory cache is always updated (so the change takes effect immediately
    for the running process); the boolean reports only whether the durable disk
    write succeeded, so the caller can surface a silent persistence failure.

    Args:
        user_id: Telegram user id.
        language: Language code; normalized via
            :func:`~src.bot.i18n.translator.normalize_language` so an unknown
            value is stored as the default.

    Returns:
        bool: True if the choice was durably persisted to disk; False if only the
            in-memory cache was updated (the disk write failed and was logged).
    """
    normalized = normalize_language(language)
    # _write_lock serializes concurrent writers so their os.replace calls land in
    # call order (newest snapshot wins on disk). The in-memory update + snapshot is
    # taken under the short _lock so readers are never blocked by the disk write;
    # the settings handler additionally offloads this whole call via
    # asyncio.to_thread so the write never runs on the event loop.
    with _write_lock:
        with _lock:
            _ensure_loaded()
            _languages[str(user_id)] = normalized
            snapshot = dict(_languages)
        return _persist(snapshot)
