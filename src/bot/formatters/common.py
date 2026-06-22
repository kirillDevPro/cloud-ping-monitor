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

# Emoji-only column headers for a per-period ping-statistics table: uptime,
# successful/total pings, average latency. No localization (universal glyphs).
STATS_METRIC_HEADERS = ["⬆️", "✓", "⚡"]


def stats_metric_cells(stats: PingStatistics) -> list[str]:
    """Return the [uptime, successful/total, avg-latency] table cells for a stats row.

    Shared by every 24-hour / per-period statistics table (monitoring detail,
    server statistics, server control card) so the metric columns stay identical.

    Args:
        stats: A non-empty statistics record.

    Returns:
        list[str]: Three cells aligned with :data:`STATS_METRIC_HEADERS` — uptime
            percentage, ``successful/total`` pings, and average latency (``—``
            when there were no successful pings).
    """
    avg = f"{stats.avg_response_time_ms:.0f}ms" if stats.successful_pings > 0 else "—"
    return [
        f"{stats.uptime_percentage:.0f}%",
        f"{stats.successful_pings}/{stats.total_pings}",
        avg,
    ]


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
