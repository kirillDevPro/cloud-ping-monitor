"""Assembles the per-language catalogs into language-keyed lookup tables.

The translations live one module per locale under ``locales/`` (``en.py``,
``ru.py``, ``uk.py``, ``es.py``), each a flat ``{key: value}`` mapping — that is
where strings are added or edited. This module only assembles them into the
language-keyed tables the translator indexes directly:

* :data:`MESSAGE_CATALOG` — ``{language: {key: template}}`` for plain strings.
* :data:`PLURAL_CATALOG` — ``{language: {key: [forms]}}`` for plural-aware keys
  (two-form locales — English, Spanish — hold ``[one, other]``; three-form
  East-Slavic locales — Russian, Ukrainian — hold ``[one, few, many]``).

Keys MUST match across locales; the i18n tests enforce that every locale defines
the same MESSAGES and PLURALS keys. Conventions for the templates themselves
(dotted namespaces, HTML markup, ``str.format`` placeholders, untranslated
code-coupled tokens) are documented in the per-locale modules.
"""

from __future__ import annotations

from .locales import en, es, ru, uk

# {language: {key: template}} for plain messages, indexed by the translator.
MESSAGE_CATALOG: dict[str, dict[str, str]] = {
    "en": en.MESSAGES,
    "ru": ru.MESSAGES,
    "uk": uk.MESSAGES,
    "es": es.MESSAGES,
}

# {language: {key: [forms]}} for plural-aware messages.
PLURAL_CATALOG: dict[str, dict[str, list[str]]] = {
    "en": en.PLURALS,
    "ru": ru.PLURALS,
    "uk": uk.PLURALS,
    "es": es.PLURALS,
}
