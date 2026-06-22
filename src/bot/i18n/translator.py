"""Lightweight runtime translator for the bot (RU/EN).

The translator resolves a message key to a localized string using the active
language stored in a :class:`contextvars.ContextVar`. A middleware sets that
context var per incoming update (see ``src/bot/middlewares/language.py``), so
keyboard builders and formatters can call :func:`t` / :func:`_` without taking a
``language`` parameter.

Two resolution modes are provided:

* :func:`t` / :func:`_` — use the language from the context var. Intended for
  request-scoped code (handlers, keyboards, formatters) that runs inside an
  update where the middleware already set the language.
* :func:`translate` — resolve an explicit language. Intended for code that is
  NOT request-scoped, most importantly background-task notifications that are
  rendered once per recipient in that recipient's own language.

Plural-aware keys live in :data:`PLURAL_CATALOG` and are resolved via
:func:`plural` / :func:`translate_plural`, which apply the Russian/English plural
rules.

The catalog lives in :mod:`src.bot.i18n.catalog`, which assembles the per-locale
modules (``locales/en.py``, ``locales/ru.py``) into the language-keyed
:data:`MESSAGE_CATALOG` / :data:`PLURAL_CATALOG` tables this module indexes; this
module only holds the resolution logic so it stays free of cyclic imports.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from .catalog import MESSAGE_CATALOG, PLURAL_CATALOG

logger = logging.getLogger(__name__)

# Supported UI languages. The first entry is the project-wide default applied to
# any user who has never explicitly picked a language.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ru", "uk")
DEFAULT_LANGUAGE: str = "en"

# Human-readable language names used on the language-picker buttons.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
    "uk": "🇺🇦 Українська",
}

# Languages that use the East-Slavic three-form plural rule (one/few/many).
_THREE_FORM_PLURAL_LANGUAGES = frozenset({"ru", "uk"})

# Active language for the current update. Defaults to DEFAULT_LANGUAGE so any
# code path that renders text without a middleware having run (tests, startup)
# still produces valid output.
_current_language: ContextVar[str] = ContextVar("current_language", default=DEFAULT_LANGUAGE)


def normalize_language(language: str | None) -> str:
    """Return a supported language code, falling back to the default.

    Args:
        language: A candidate language code (may be None or unsupported).

    Returns:
        str: ``language`` if it is one of :data:`SUPPORTED_LANGUAGES`, otherwise
            :data:`DEFAULT_LANGUAGE`.
    """
    if language in SUPPORTED_LANGUAGES:
        return language  # type: ignore[return-value]
    return DEFAULT_LANGUAGE


def set_current_language(language: str | None) -> None:
    """Set the active language for the current update/context.

    Args:
        language: Language code to activate; normalized via
            :func:`normalize_language` so an unknown value becomes the default.

    Returns:
        None.
    """
    _current_language.set(normalize_language(language))


def get_current_language() -> str:
    """Return the active language for the current update/context.

    Returns:
        str: The language code currently set in the context var.
    """
    return _current_language.get()


def _lookup(key: str, language: str) -> str:
    """Resolve a plain (non-plural) message key for a language.

    Falls back to the default language, then to the raw key, logging a warning so
    a missing translation surfaces during review/testing instead of silently
    shipping an English-or-key string.

    Args:
        key: Message key present in the per-locale tables of :data:`MESSAGE_CATALOG`.
        language: Already-normalized language code.

    Returns:
        str: The localized template (before any ``.format`` substitution).
    """
    value = MESSAGE_CATALOG.get(language, {}).get(key)
    if value is None:
        value = MESSAGE_CATALOG[DEFAULT_LANGUAGE].get(key)
    if value is None:
        logger.warning("Missing i18n message key: %r", key)
        return key
    return value


def _format(template: str, kwargs: dict[str, object]) -> str:
    """Apply ``str.format`` to a template, tolerating a malformed catalog entry.

    A KeyError/IndexError here means the catalog template references a placeholder
    the caller did not supply (a catalog bug); the unformatted template is
    returned and the error logged rather than crashing the handler.

    Args:
        template: The localized template string.
        kwargs: Substitution values for ``str.format``.

    Returns:
        str: The formatted string, or the raw template on a formatting error.
    """
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("i18n format error for template %r: %s", template, exc)
        return template


def translate(key: str, language: str | None = None, /, **kwargs: object) -> str:
    """Resolve a message key for an explicit language.

    Use this for code that is not request-scoped (background-task notifications
    rendered per recipient). Request-scoped code should prefer :func:`t`.

    Args:
        key: Message key (see :data:`MESSAGE_CATALOG`).
        language: Target language; normalized and defaulted when None/unknown.
        **kwargs: Optional substitution values for ``str.format``.

    Returns:
        str: The localized, formatted string.
    """
    lang = normalize_language(language)
    return _format(_lookup(key, lang), kwargs)


def t(key: str, /, **kwargs: object) -> str:
    """Resolve a message key for the active (context-var) language.

    Args:
        key: Message key (see :data:`MESSAGE_CATALOG`).
        **kwargs: Optional substitution values for ``str.format``.

    Returns:
        str: The localized, formatted string.
    """
    return _format(_lookup(key, get_current_language()), kwargs)


# Conventional gettext-style alias for the active-language resolver.
_ = t


def _plural_form_index(language: str, n: int) -> int:
    """Return the index of the plural form to use for a count.

    Russian and Ukrainian use three forms (one / few / many) with the same
    East-Slavic rule; English uses two (one / other).

    Args:
        language: Already-normalized language code.
        n: The count selecting the plural form.

    Returns:
        int: Index into the language's form list for this key.
    """
    if language in _THREE_FORM_PLURAL_LANGUAGES:
        n_abs = abs(n)
        tens = n_abs % 100
        ones = n_abs % 10
        if 11 <= tens <= 14:
            return 2  # many: 11..14
        if ones == 1:
            return 0  # one: 1, 21, 31, ...
        if 2 <= ones <= 4:
            return 1  # few: 2..4, 22..24, ...
        return 2  # many: 0, 5..20, ...
    # English (and the fallback default): one vs other.
    return 0 if n == 1 else 1


def translate_plural(key: str, n: int, language: str | None = None, /, **kwargs: object) -> str:
    """Resolve a plural-aware key for an explicit language.

    The per-locale plural lists in :data:`PLURAL_CATALOG` hold the ordered plural
    forms (``[one, other]`` for English, ``[one, few, many]`` for Russian).
    ``n`` is exposed to the template as ``{n}`` in addition to ``**kwargs``.

    Args:
        key: Plural key (see :data:`PLURAL_CATALOG`).
        n: The count selecting the plural form (also passed to ``.format`` as n).
        language: Target language; normalized and defaulted when None/unknown.
        **kwargs: Optional extra substitution values for ``str.format``.

    Returns:
        str: The localized, formatted plural string.
    """
    lang = normalize_language(language)
    forms = PLURAL_CATALOG.get(lang, {}).get(key) or PLURAL_CATALOG[DEFAULT_LANGUAGE].get(key)
    if not forms:
        logger.warning("Missing i18n plural key: %r", key)
        return key
    index = min(_plural_form_index(lang, n), len(forms) - 1)
    return _format(forms[index], {"n": n, **kwargs})


def plural(key: str, n: int, /, **kwargs: object) -> str:
    """Resolve a plural-aware key for the active (context-var) language.

    Args:
        key: Plural key (see :data:`PLURAL_CATALOG`).
        n: The count selecting the plural form (also passed to ``.format`` as n).
        **kwargs: Optional extra substitution values for ``str.format``.

    Returns:
        str: The localized, formatted plural string.
    """
    return translate_plural(key, n, get_current_language(), **kwargs)


def menu_variants(key: str) -> set[str]:
    """Return every localized value of a key across all supported languages.

    Reply-keyboard buttons are matched by their exact text, so a handler bound to
    a menu button must accept the button label in any language. This returns the
    full set of labels for that menu key for use by the
    :class:`~src.bot.filters.menu.MainMenuButton` filter.

    Args:
        key: Message key for a reply-keyboard label (see :data:`MESSAGE_CATALOG`).

    Returns:
        set[str]: All localized labels for the key.
    """
    variants = {
        MESSAGE_CATALOG[lang][key]
        for lang in SUPPORTED_LANGUAGES
        if key in MESSAGE_CATALOG.get(lang, {})
    }
    if not variants:
        logger.warning("Missing i18n menu key: %r", key)
    return variants
