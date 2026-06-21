"""Shared formatting utilities."""

from aiogram import html


def esc(value: object) -> str:
    """HTML-escape a dynamic value for safe interpolation into an HTML message.

    The bot uses parse_mode=HTML, so any externally-sourced string (server name,
    IP, region, plan, OS, error text, provider/display names) interpolated raw
    would let an HTML metacharacter (``<``, ``>``, ``&``) make Telegram reject the
    whole message with a 400 parse error. Wrap every such value with esc().

    Args:
        value: The value to escape (None becomes an empty string).

    Returns:
        str: The HTML-escaped string.
    """
    return html.quote("" if value is None else str(value))


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
    Convert a number of days into the format 'X г. Y мес.' or 'X мес.'.

    Args:
        days: Number of days

    Returns:
        str: Formatted string, or an empty string if less than a month
    """
    if days < 30:
        return ""  # Too few to express in months

    months_total = days // 30
    years = months_total // 12
    months = months_total % 12

    if years > 0 and months > 0:
        return f"{years} г. {months} мес."
    elif years > 0:
        return f"{years} г."
    else:
        return f"{months} мес."
