"""Shared formatting utilities."""

import re

from aiogram import html

from ...models import PingStatistics
from ..i18n import _

# Inline formatting tags stripped when a catalog label must become plain text
# (e.g. a <details> summary, which Telegram shows escaped — tags would render
# literally). Covers the full set of Bot API inline tags so a future locale that
# adds <u>/<s> to a summary label is handled too.
_INLINE_TAG_RE = re.compile(r"</?(?:b|i|u|s|code)>")


# Provider type -> colored UI circle, matched by alias prefix. Keeps the on-screen
# marker consistent wherever only the alias (not the ASCII log emoji) is at hand.
_PROVIDER_EMOJI_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("hetzner", "🔴"),
    ("vultr", "🔵"),
    ("aws", "🟠"),
)


def provider_emoji(alias: str) -> str:
    """Return the colored UI circle for a provider alias (by hetzner/vultr/aws prefix).

    Args:
        alias: Provider alias (e.g. "vultr_main", "hetzner_prod", "aws_main").

    Returns:
        str: The matching colored circle, or a cloud fallback for an unknown prefix.
    """
    lowered = alias.lower()
    for prefix, emoji in _PROVIDER_EMOJI_BY_PREFIX:
        if lowered.startswith(prefix):
            return emoji
    return "☁️"


def stats_metric_line(stats: PingStatistics) -> str:
    """Return one compact stats line: uptime · successful/total · errors · latency.

    Shared by every 24-hour / per-period statistics display (monitoring detail,
    server statistics, server control card) so the metrics read identically. Emoji
    anchors keep it scannable without a table. The failure (🔴) and timeout (⏱)
    segments appear only when non-zero, so a healthy period stays short while a bad
    one still distinguishes ICMP failures from timeouts; latency is shown only when
    there was at least one successful ping.

    Args:
        stats: A non-empty statistics record.

    Returns:
        str: e.g. ``⬆ 100% · ✓ 381/381 · ⚡ 24ms`` (healthy) or
            ``⬆ 95% · ✓ 95/100 · 🔴 3 · ⏱ 2 · ⚡ 150ms`` (with failures/timeouts).
    """
    parts = [
        f"⬆ {stats.uptime_percentage:.0f}%",
        f"✓ {stats.successful_pings}/{stats.total_pings}",
    ]
    if stats.failed_pings > 0:
        parts.append(f"🔴 {stats.failed_pings}")
    if stats.timeout_pings > 0:
        parts.append(f"⏱ {stats.timeout_pings}")
    if stats.successful_pings > 0:
        parts.append(f"⚡ {stats.avg_response_time_ms:.0f}ms")
    return " · ".join(parts)


def esc(value: object) -> str:
    """HTML-escape a dynamic value for safe interpolation into an HTML message.

    The bot sends rich HTML messages (Bot API 10.1), so any externally-sourced
    string (server name, IP, region, plan, OS, error text, provider/display names)
    interpolated raw would let an HTML metacharacter (``<``, ``>``, ``&``) make
    Telegram reject the whole message with a 400 parse error — the same escaping
    rules as the classic HTML parse mode apply. Wrap every such value with esc().

    Args:
        value: The value to escape (None becomes an empty string).

    Returns:
        str: The HTML-escaped string.
    """
    return html.quote("" if value is None else str(value))


def strip_rule(label: str) -> str:
    """Drop the legacy ``━━━ ... ━━━`` section-rule decoration, keeping inline tags.

    The flat classic layout framed section headers with box-drawing rules
    (``━━━ <b>Finances</b> ━━━``). The rich layout uses real structure (tables,
    blank-line blocks) instead, so the rule glyphs are removed at render time
    while the bold label inside is preserved. Lets the rich formatters reuse the
    existing catalog keys unchanged.

    Args:
        label: A catalog section-header string, possibly wrapped in ``━━━`` rules.

    Returns:
        str: The label with all ``━`` glyphs removed and surrounding whitespace
            trimmed (inline ``<b>``/``<i>`` tags kept).
    """
    return label.replace("━", "").strip()


def plain(label: str) -> str:
    """Reduce a catalog label to plain text for use as a ``<details>`` summary.

    A ``<summary>`` is HTML-escaped by the rich builder, so any ``<b>``/``<i>``
    tags in the source label would show as literal text. This strips those inline
    tags and the ``━━━`` rule decoration, leaving the words (and any emoji) only.

    Args:
        label: A catalog label that may contain inline tags and/or ``━━━`` rules.

    Returns:
        str: The label as plain text (inline tags and ``━`` glyphs removed,
            whitespace trimmed).
    """
    return _INLINE_TAG_RE.sub("", label.replace("━", "")).strip()


def format_number(value: int) -> str:
    """
    Format an integer with a space as the thousands separator.

    Numbers >= 1000: 3 596, 1 234 567
    Numbers < 1000: 999, 0

    Args:
        value: Number to format

    Returns:
        Formatted string
    """
    if value >= 1000:
        return f"{value:,}".replace(",", " ")
    return str(value)


def format_days_as_period(days: int) -> str:
    """
    Convert a number of days into a localized years/months period string.

    Args:
        days: Number of days.

    Returns:
        str: Localized period string, or an empty string when less than a month.
    """
    if days < 30:
        return ""  # Too few to express in months

    months_total = days // 30
    years = months_total // 12
    months = months_total % 12

    if years > 0 and months > 0:
        return _("period.years_months", years=years, months=months)
    elif years > 0:
        return _("period.years", years=years)
    else:
        return _("period.months", months=months)
