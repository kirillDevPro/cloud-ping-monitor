#!/usr/bin/env python3
"""CI guard: verify the i18n locale catalogs are complete and in sync.

Exits non-zero (failing the build) when any of these hold:

1. The locales do not define exactly the same ``MESSAGES`` / ``PLURALS`` keys
   (a key present in one language but missing in another).
2. Any translation value is empty or whitespace-only (an untranslated field).
3. A plural entry has the wrong number of forms (per PLURAL_FORM_COUNT:
   English/Spanish = 2 one/other, Russian/Ukrainian = 3 one/few/many).
4. An i18n key referenced in the code (``_("...")``, ``translate("...")``,
   ``render_message("...")``, ``title_key="..."``, ``MainMenuButton("...")``, …)
   is missing from the catalog — which would silently show users the raw key.

The locale modules are loaded in ISOLATION (no aiogram / project import needed),
and the code is scanned via the AST (no false positives from comments/strings),
so the check is fast and unaffected by unrelated import errors.

Run locally:  python scripts/check_i18n_locales.py
"""

from __future__ import annotations

import ast
import importlib.util
import re
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = ROOT / "src" / "bot" / "i18n" / "locales"
SRC_DIR = ROOT / "src"

# Locales to compare and the exact plural-form count each requires.
SUPPORTED_LANGUAGES = ("en", "ru", "uk", "es")
PLURAL_FORM_COUNT = {"en": 2, "ru": 3, "uk": 3, "es": 2}

# A dotted lower-case identifier — the shape of every catalog key ("menu.monitoring").
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")

# Translator call names whose FIRST positional string argument is a catalog key.
# Scanned WITHOUT a namespace filter, so a direct call into a brand-new namespace
# (e.g. translate("billing.x")) is still validated even before that namespace exists.
KEY_FUNCS = {
    "_",
    "t",
    "translate",
    "plural",
    "translate_plural",
    "render_message",
    "render_plural",
    "menu_variants",
    "MainMenuButton",
}
# Keyword arguments whose string value is a catalog key.
KEY_KWARGS = {"title_key"}

# Files that DEFINE the catalog (keys + values) rather than reference it; excluded
# from the reference scan so key definitions are not mistaken for references.
_CATALOG_DEFINITION_FILE = SRC_DIR / "bot" / "i18n" / "catalog.py"


def _scan_targets() -> list[Path]:
    """Return the project code files to scan for i18n key references.

    main.py lives at the repo root (outside src/) yet calls the translator
    (bot commands, the providers-unavailable alert), so it MUST be scanned too.
    """
    targets: list[Path] = []
    main_py = ROOT / "main.py"
    if main_py.exists():
        targets.append(main_py)
    targets.extend(sorted(SRC_DIR.rglob("*.py")))
    return targets


def _load_locale(lang: str):
    """Load a locale module in isolation (without importing the bot package)."""
    path = LOCALES_DIR / f"{lang}.py"
    spec = importlib.util.spec_from_file_location(f"_locale_{lang}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load locale module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_key_parity(locales: dict, attr: str, errors: list[str]) -> set[str]:
    """Check that every locale defines the same set of keys for ``attr``."""
    per_lang = {lang: set(getattr(mod, attr)) for lang, mod in locales.items()}
    all_keys: set[str] = set().union(*per_lang.values())
    for lang, keys in per_lang.items():
        for missing in sorted(all_keys - keys):
            errors.append(f"{attr}: key {missing!r} missing in locale '{lang}'")
    return all_keys


def _check_non_empty(locales: dict, errors: list[str]) -> None:
    """Check that no MESSAGES value or plural form is empty/whitespace-only."""
    for lang, mod in locales.items():
        for key, value in mod.MESSAGES.items():
            if not isinstance(value, str) or not value.strip():
                errors.append(f"MESSAGES: key {key!r} in '{lang}' is empty/blank")
        for key, forms in mod.PLURALS.items():
            for idx, form in enumerate(forms):
                if not isinstance(form, str) or not form.strip():
                    errors.append(f"PLURALS: key {key!r} form {idx} in '{lang}' is empty/blank")


def _check_plural_form_counts(locales: dict, errors: list[str]) -> None:
    """Check that each locale's plural lists have the expected number of forms."""
    for lang, mod in locales.items():
        expected = PLURAL_FORM_COUNT.get(lang)
        if expected is None:
            continue
        for key, forms in mod.PLURALS.items():
            if not isinstance(forms, list) or len(forms) != expected:
                got = len(forms) if isinstance(forms, list) else "non-list"
                errors.append(
                    f"PLURALS: key {key!r} in '{lang}' has {got} forms, expected {expected}"
                )


def _format_fields(template: str) -> set[str]:
    """Return the set of ``str.format`` field names used in a template."""
    try:
        return {name for _, name, _, _ in string.Formatter().parse(template) if name}
    except ValueError:
        return {"<malformed>"}


def _check_placeholder_parity(locales: dict, errors: list[str]) -> None:
    """Check that each key uses the same ``{placeholder}`` set in every locale.

    A locale that drops or renames a placeholder would silently render a wrong /
    unformatted string, so the field-name sets must match the reference locale.
    """
    langs = list(locales)
    ref = langs[0]
    for key, template in locales[ref].MESSAGES.items():
        ref_fields = _format_fields(template)
        for lang in langs[1:]:
            other = locales[lang].MESSAGES.get(key)
            if other is not None and _format_fields(other) != ref_fields:
                errors.append(
                    f"MESSAGES: key {key!r} placeholders differ between '{ref}' "
                    f"({sorted(ref_fields)}) and '{lang}' ({sorted(_format_fields(other))})"
                )
    for key, forms in locales[ref].PLURALS.items():
        # EVERY plural form (in every locale) must carry the same placeholder set as
        # the reference locale's first form. A union-across-forms check would miss one
        # form dropping {n} while a sibling form still has it — rendering a wrong count.
        canonical = _format_fields(forms[0])
        for lang in langs:
            other_forms = locales[lang].PLURALS.get(key)
            if other_forms is None:
                continue
            for idx, form in enumerate(other_forms):
                fields = _format_fields(form)
                if fields != canonical:
                    errors.append(
                        f"PLURALS: key {key!r} form {idx} in '{lang}' has placeholders "
                        f"{sorted(fields)}, expected {sorted(canonical)}"
                    )


def _check_template_validity(locales: dict, errors: list[str]) -> None:
    """Reject malformed or auto-positional format templates in any locale.

    Placeholder parity only compares field SETS across locales, so an identically
    malformed template (unbalanced braces) or an auto-positional ``{}`` / ``{0}``
    field would pass parity yet break at runtime ``str.format(**kwargs)``. This
    validates every template on its own, independent of cross-locale parity.
    """

    def validate(label: str, lang: str, key: str, template: str) -> None:
        try:
            parsed = list(string.Formatter().parse(template))
        except ValueError:
            errors.append(f"{label}: key {key!r} in '{lang}' has a malformed format template")
            return
        for _literal, field_name, spec, _conv in parsed:
            if field_name is None:
                continue  # plain-text segment, no placeholder
            root = field_name.split(".", 1)[0].split("[", 1)[0]
            if field_name == "" or root.isdigit():
                errors.append(
                    f"{label}: key {key!r} in '{lang}' uses a positional '{{}}' field; "
                    "named placeholders are required"
                )
            # Nested replacement fields live in the format spec ({value:{0}}); a
            # positional one there also breaks str.format, so validate it too.
            if spec:
                validate(label, lang, key, spec)

    for lang, mod in locales.items():
        for key, template in mod.MESSAGES.items():
            validate("MESSAGES", lang, key, template)
        for key, forms in mod.PLURALS.items():
            for idx, form in enumerate(forms):
                validate("PLURALS", lang, f"{key}[{idx}]", form)


def _referenced_keys(namespaces: set[str]) -> dict[str, Path]:
    """Scan project code via the AST and return every referenced key -> a file.

    Two complementary passes (deduplicated):

    * PRECISE — the first string argument of a known translator call
      (``_``/``translate``/``MainMenuButton``/…) and ``title_key=`` kwargs. These are
      DEFINITELY keys, so they are collected WITHOUT a namespace filter — that catches
      a direct call into a brand-new namespace before it exists in the catalog.
    * BROAD — any catalog-key-shaped string literal whose first segment is a KNOWN
      namespace. This catches INDIRECT references (keys stored in module-level maps
      like ``_TREND_KEYS`` / ``_ACTION_DONE_KEYS`` and passed to the translator) while
      the namespace gate keeps unrelated dotted strings (module paths, etc.) out.

    The locale/catalog definition files are skipped — they define keys, not reference
    them.
    """
    found: dict[str, Path] = {}
    for path in _scan_targets():
        if path == _CATALOG_DEFINITION_FILE or LOCALES_DIR in path.parents:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # PRECISE: translator-call key (positional first arg OR key= keyword) and
            # global key-bearing kwargs (title_key=). No namespace gate, so a direct
            # call into a brand-new namespace is still validated.
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                candidates: list[ast.expr] = []
                if name in KEY_FUNCS:
                    candidates += node.args[:1]
                    candidates += [kw.value for kw in node.keywords if kw.arg == "key"]
                candidates += [kw.value for kw in node.keywords if kw.arg in KEY_KWARGS]
                for candidate in candidates:
                    if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                        found.setdefault(candidate.value, path)
            # BROAD: any key-shaped literal whose namespace is already known.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if KEY_PATTERN.match(value) and value.split(".", 1)[0] in namespaces:
                    found.setdefault(value, path)
    return found


def _check_referenced_keys_exist(valid_keys: set[str], errors: list[str]) -> None:
    """Check every i18n key referenced in code (directly or indirectly) exists."""
    namespaces = {key.split(".", 1)[0] for key in valid_keys}
    for key, path in sorted(_referenced_keys(namespaces).items()):
        if key not in valid_keys:
            rel = path.relative_to(ROOT)
            errors.append(f"REFERENCE: key {key!r} used in {rel} is missing from the catalog")


def main() -> int:
    """Run all i18n checks; print a report and return a process exit code."""
    locales = {lang: _load_locale(lang) for lang in SUPPORTED_LANGUAGES}
    errors: list[str] = []

    message_keys = _check_key_parity(locales, "MESSAGES", errors)
    plural_keys = _check_key_parity(locales, "PLURALS", errors)
    _check_non_empty(locales, errors)
    _check_plural_form_counts(locales, errors)
    _check_placeholder_parity(locales, errors)
    _check_template_validity(locales, errors)
    _check_referenced_keys_exist(message_keys | plural_keys, errors)

    if errors:
        print(f"[FAIL] i18n locale check found {len(errors)} problem(s):")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        f"[OK] i18n locales in sync: {len(message_keys)} messages + {len(plural_keys)} "
        f"plurals across {', '.join(SUPPORTED_LANGUAGES)}; all values present; "
        "all referenced keys exist."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
