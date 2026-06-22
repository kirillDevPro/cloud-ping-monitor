"""Bot API 10.1 rich-message rendering — the bot's ONLY message path.

Every screen and every admin notification is rendered as a Telegram *rich
message* (Bot API 10.1, aiogram 3.29): ``bot.send_rich_message`` /
``message.answer_rich`` / ``message.edit_text(rich_message=...)`` carrying an
:class:`~aiogram.types.InputRichMessage`. This is a separate transport from the
classic ``parse_mode=HTML`` text path and unlocks document-grade structure —
tables, collapsible ``<details>``, headings, ``<br>`` line breaks.

There is deliberately NO classic fallback and NO feature flag: rich is the only
path. A payload Telegram rejects raises ``TelegramBadRequest`` and propagates to
the normal error handlers instead of being silently re-sent as plain text.

This module owns the rich tag vocabulary in ONE place so the markup is built
consistently (and a tweak is local), plus the three send/edit entry points the
rest of the bot calls:

* builders — :func:`stack`, :func:`table`, :func:`details`, :func:`to_rich`; and
* transport — :func:`answer_rich`, :func:`edit_rich`, :func:`send_rich`.

The line-break rule (the one rich gotcha): in rich HTML a bare ``\\n`` between two
text segments collapses to a single space, exactly like an HTML renderer, so
vertical stacking MUST use ``<br>``. Structure built here uses ``<br>`` (via
:func:`stack`) or self-separating block elements; :func:`to_rich` converts the
residual ``\\n`` still present in catalog/formatter strings to ``<br>`` at the
transport boundary so existing templates keep rendering line-by-line.
"""

import logging
from collections.abc import Iterable, Sequence

from aiogram import Bot, html
from aiogram.types import (
    InaccessibleMessage,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
    ReplyKeyboardMarkup,
)

logger = logging.getLogger(__name__)


def esc(value: object) -> str:
    """HTML-escape a dynamic value for safe interpolation into rich HTML.

    Rich messages are HTML, so the same escaping rules as the classic HTML
    parse mode apply: any externally-sourced value (server name, IP, region,
    provider/display name, error text) interpolated raw could let an HTML
    metacharacter (``&``, ``<``, ``>``) corrupt the markup. The rich builders
    (:func:`table`, :func:`details`, :func:`heading`) escape their leaf inputs
    with this; :func:`stack` joins already-built HTML fragments verbatim.

    A local copy of ``formatters.common.esc`` is kept here on purpose: this
    module is a lower layer than ``formatters`` (the formatters import these
    builders), so it must not import from ``formatters`` or a cycle forms.

    Args:
        value: The value to escape (None becomes an empty string).

    Returns:
        str: The HTML-escaped string.
    """
    return html.quote("" if value is None else str(value))


def to_rich(html_text: str) -> str:
    """Convert a classic-HTML/template string into rich HTML.

    Rich HTML collapses a bare ``\\n`` to a single space, so every newline the
    formatters and i18n catalog templates use for vertical stacking is rewritten
    to ``<br>``. A blank-line paragraph break (``\\n\\n``) becomes ``<br><br>``,
    preserving the intended spacing.

    Structure built with :func:`table` / :func:`details` is already ``\\n``-free
    (its block elements separate themselves), so this conversion only affects the
    plain text lines coming from templates. Two consistent ways to compose exist —
    use :func:`stack` all the way up (``<br>``-joined, this is a no-op on it), or
    join legacy template fragments with ``\\n`` (converted here); do NOT ``\\n``-join
    fragments that :func:`stack` already separated with ``<br>`` or the join breaks
    will collapse.

    Args:
        html_text: A rich-or-classic HTML string whose newlines mark line breaks.

    Returns:
        str: The same markup with every ``\\n`` replaced by ``<br>``.
    """
    return html_text.replace("\n", "<br>")


def stack(*parts: object) -> str:
    """Join non-empty parts vertically with ``<br>`` (the rich line break).

    The rich-message replacement for ``"\\n".join(...)``: a bare ``\\n`` collapses
    to a space in rich HTML, so vertical stacking must use ``<br>``. Falsy parts
    (``None`` / empty string) are dropped so a conditional section contributes no
    blank line when absent.

    Parts are inserted VERBATIM — pass already-escaped text or trusted catalog
    HTML / builder output (``table`` / ``details``), not raw user data.

    Args:
        *parts: HTML fragments to stack; falsy entries are skipped.

    Returns:
        str: The parts joined by ``<br>``.
    """
    return "<br>".join(str(p) for p in parts if p)


def blocks(*parts: object) -> str:
    """Join non-empty parts as paragraphs separated by a blank line (``<br><br>``).

    The section-level companion to :func:`stack`: where ``stack`` puts adjacent
    lines on consecutive rows, ``blocks`` leaves a blank line between whole
    sections (a heading + its table, the next heading + its lines, ...). Falsy
    parts (an absent conditional section) are dropped so no double blank line
    appears. Parts are inserted VERBATIM — pass built rich HTML, not raw data.

    Args:
        *parts: Section HTML fragments to separate by a blank line; falsy entries
            are skipped.

    Returns:
        str: The parts joined by ``<br><br>``.
    """
    return "<br><br>".join(str(p) for p in parts if p)


def table(headers: Sequence[object], rows: Iterable[Sequence[object]]) -> str:
    """Build a rich ``<table>`` with a header row and body rows.

    Header cells and body cells are HTML-escaped (leaf values), so pass raw
    values — names, counts, amounts, status glyphs — not pre-escaped or marked-up
    strings. Each row is normalized to the header width: extra cells are dropped
    and short rows are padded with empty cells, so a ragged row can never produce
    malformed markup. Telegram caps a rich table at 20 columns.

    Args:
        headers: The header cell values (defines the column count).
        rows: An iterable of rows, each an iterable of cell values.

    Returns:
        str: A single-line ``<table>...</table>`` string (no ``\\n``, so it is
            safe to combine with :func:`stack` and pass through :func:`to_rich`).
    """
    width = len(headers)
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)

    body_rows: list[str] = []
    for row in rows:
        cells = list(row)[:width]
        cells += [""] * (width - len(cells))
        body_rows.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")

    return (
        f"<table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )


def details(summary: str, body_html: str, *, is_open: bool = False) -> str:
    """Build a collapsible ``<details>`` block.

    The ``<summary>`` label is always shown; the body is revealed on tap. The
    summary is HTML-escaped (a leaf value); ``body_html`` is inserted VERBATIM,
    so it must already be valid rich HTML (e.g. :func:`stack` / :func:`table`
    output), with any dynamic leaf values escaped by the builder that produced it.

    Args:
        summary: The always-visible label (HTML-escaped).
        body_html: The collapsible body as ready rich HTML (inserted verbatim).
        is_open: Render expanded instead of collapsed (adds the ``open``
            attribute).

    Returns:
        str: ``<details ...><summary>...</summary>body</details>``.
    """
    attr = " open" if is_open else ""
    return f"<details{attr}><summary>{esc(summary)}</summary>{body_html}</details>"


def _input(html_text: str) -> InputRichMessage:
    """Wrap rich HTML into an InputRichMessage for a send/edit call.

    ``skip_entity_detection=True`` is set on every payload: the bot's content is
    fully explicit markup (table cells of IPs, aliases, amounts, timestamps), and
    without it Telegram would auto-linkify bare tokens into URLs / @mentions /
    phone numbers / hashtags. Explicit tags (``<b>``, ``<a>``, ...) are unaffected.

    Args:
        html_text: The rich HTML body (run through :func:`to_rich` here so callers
            may pass templates that still contain ``\\n`` line breaks).

    Returns:
        InputRichMessage: The payload for ``send_rich_message`` /
            ``edit_message_text`` / ``answer_rich``.
    """
    return InputRichMessage(html=to_rich(html_text), skip_entity_detection=True)


async def answer_rich(
    message: Message | InaccessibleMessage,
    html_text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> Message:
    """Send a NEW rich message in the chat of ``message`` (the rich ``answer``).

    Replaces ``message.answer(text, ...)`` on the rich path: sends a fresh rich
    message via ``answer_rich`` (auto-targeting the message's chat / thread).
    Accepts an ``InaccessibleMessage`` source (a too-old callback message): its
    inherited ``answer_rich`` shortcut still targets the chat, exactly as the
    classic ``.answer`` did in ``reset_screen_from_callback``.

    Args:
        message: The incoming message whose chat (and topic thread) to reply in.
        html_text: The rich HTML body (newlines are converted to ``<br>``).
        reply_markup: Optional inline or reply keyboard.

    Returns:
        Message: The sent rich message.

    Raises:
        TelegramAPIError: Propagated if the send fails (no classic fallback).
    """
    return await message.answer_rich(_input(html_text), reply_markup=reply_markup)


async def edit_rich(
    message: Message,
    html_text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit ``message`` in place to a rich body (the rich ``edit_text``).

    Replaces ``message.edit_text(text, ...)`` on the rich path: ``text`` is
    omitted and the new content is supplied as ``rich_message`` instead. Edit
    pre-checks and the "message is not modified" guard stay with the caller
    (:func:`~src.bot.utils.message_editor.safe_edit_message`).

    Args:
        message: The message to edit (must be accessible / editable).
        html_text: The new rich HTML body (newlines are converted to ``<br>``).
        reply_markup: Optional new inline keyboard.

    Returns:
        None.

    Raises:
        TelegramBadRequest: Propagated (e.g. "message is not modified") for the
            caller to interpret; other Telegram errors propagate too.
    """
    await message.edit_text(rich_message=_input(html_text), reply_markup=reply_markup)


async def send_rich(
    bot: Bot,
    chat_id: int,
    html_text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> Message:
    """Send a rich message to an explicit ``chat_id`` (no source Message).

    Used by background-task notifications, which broadcast to admin chat ids
    rather than replying to an incoming message. Replaces
    ``bot.send_message(chat_id, text)`` on the rich path.

    Args:
        bot: The Bot instance performing the send.
        chat_id: Target chat id (an admin's id).
        html_text: The rich HTML body (newlines are converted to ``<br>``).
        reply_markup: Optional keyboard.

    Returns:
        Message: The sent rich message.

    Raises:
        TelegramAPIError: Propagated to the caller (no classic fallback).
    """
    return await bot.send_rich_message(
        chat_id=chat_id, rich_message=_input(html_text), reply_markup=reply_markup
    )
