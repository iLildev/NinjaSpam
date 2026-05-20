"""
core/helpers/string_handling.py — Text parsing utilities shared across plugins.

Provides:
- extract_time          : parse "10m" / "2h" / "1d" into a future UTC datetime
- split_quotes          : split a string respecting quoted phrases
- markdown_parser       : escape invalid Markdown while preserving valid entities
- button_markdown_parser: parse [label](buttonurl:url) syntax into (text, buttons)
- escape_invalid_curly_brackets: safely escape {placeholders} in template strings
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from telegram import Message, MessageEntity


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

def extract_time(time_val: str) -> Optional[datetime]:
    """
    Parse a compact time string (e.g. ``"10m"``, ``"2h"``, ``"3d"``) into an
    absolute UTC ``datetime`` object representing when the action should expire.

    Supported units:
    - ``m`` — minutes
    - ``h`` — hours
    - ``d`` — days

    Returns ``None`` when the format is invalid rather than raising, so callers
    can reply with a helpful error message themselves.

    Examples::

        extract_time("30m")  # → utcnow() + 30 minutes
        extract_time("2h")   # → utcnow() + 2 hours
        extract_time("7d")   # → utcnow() + 7 days
        extract_time("foo")  # → None
    """
    if len(time_val) < 2:
        return None

    unit: str = time_val[-1].lower()
    numeric_part: str = time_val[:-1]

    if not numeric_part.isdigit():
        return None

    amount: int = int(numeric_part)

    if unit == "m":
        delta = timedelta(minutes=amount)
    elif unit == "h":
        delta = timedelta(hours=amount)
    elif unit == "d":
        delta = timedelta(days=amount)
    else:
        return None

    return datetime.now(tz=timezone.utc) + delta


# ---------------------------------------------------------------------------
# Quote-aware splitting
# ---------------------------------------------------------------------------

_SMART_OPEN: str = "\u201c"   # "
_SMART_CLOSE: str = "\u201d"  # "
_START_CHARS: Tuple[str, ...] = ("'", '"', _SMART_OPEN)


def split_quotes(text: str) -> List[str]:
    """
    Split ``text`` into at most two parts, treating a leading quote character
    as a phrase delimiter.

    Examples::

        split_quotes('"hello world" reason')  → ['hello world', 'reason']
        split_quotes('keyword rest')           → ['keyword', 'rest']
    """
    if not any(text.startswith(char) for char in _START_CHARS):
        return text.split(None, 1)

    # Walk forward, respecting backslash escapes, to find the closing quote.
    close_char: str = _SMART_CLOSE if text[0] == _SMART_OPEN else text[0]
    counter: int = 1
    while counter < len(text):
        if text[counter] == "\\":
            counter += 1  # skip the escaped character
        elif text[counter] == close_char:
            break
        counter += 1
    else:
        # No closing quote found — fall back to plain split.
        return text.split(None, 1)

    key: str = _remove_escapes(text[1:counter].strip())
    rest: str = text[counter + 1:].strip()

    if not key:
        key = text[0] + text[0]

    return list(filter(None, [key, rest]))


def _remove_escapes(text: str) -> str:
    """Strip backslash escape characters from ``text``."""
    result: str = ""
    escaped: bool = False
    for char in text:
        if escaped:
            result += char
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            result += char
    return result


# ---------------------------------------------------------------------------
# Markdown parser (preserves valid entities, escapes stray special chars)
# ---------------------------------------------------------------------------

# Matches intentional Markdown: *bold*, _italic_, `code`, [text](url)
# Captures a group named 'esc' for unmatched *, _, `, [ characters.
_MATCH_MD = re.compile(
    r"\*(.*?)\*|"
    r"_(.*?)_|"
    r"`(.*?)`|"
    r"(?<!\\)(\[.*?\])(\(.*?\))|"
    r"(?P<esc>[*_`\[])"
)

# Finds standard []() links so we know which URLs are already formatted.
_LINK_REGEX = re.compile(r"(?<!\\)\[.+?\]\((.*?)\)")

# Finds button syntax: [label](buttonurl:url) or [label](buttonurl:url:same)
_BTN_URL_REGEX = re.compile(r"(\[([^\[]+?)\]\(buttonurl:(?:/{0,2})(.+?)(:same)?\))")


def _selective_escape(to_parse: str) -> str:
    """Escape unmatched Markdown special characters in ``to_parse``."""
    offset: int = 0
    for match in _MATCH_MD.finditer(to_parse):
        if match.group("esc"):
            pos: int = match.start() + offset
            to_parse = to_parse[:pos] + "\\" + to_parse[pos:]
            offset += 1
    return to_parse


def markdown_parser(
    txt: str,
    entities: Optional[Dict[MessageEntity, str]] = None,
    offset: int = 0,
) -> str:
    """
    Parse ``txt``, preserving intentional Telegram Markdown (code blocks,
    URLs, text links) while escaping any stray special characters that would
    break rendering.

    ``entities`` is the dict returned by ``Message.parse_entities()``.
    ``offset`` compensates for the command + notename prefix stripped by the
    caller before passing the text.
    """
    if not entities:
        entities = {}
    if not txt:
        return ""

    prev: int = 0
    res: str = ""

    for ent, ent_text in entities.items():
        if ent.offset < -offset:
            continue

        start: int = ent.offset + offset
        end: int = ent.offset + offset + ent.length - 1

        if ent.type in ("code", "url", "text_link"):
            if ent.type == "url":
                # Don't escape URLs already wrapped in []()
                if any(
                    m.start(1) <= start and end <= m.end(1)
                    for m in _LINK_REGEX.finditer(txt)
                ):
                    continue
                res += _selective_escape(txt[prev:start] or "") + _escape_md(ent_text)
            elif ent.type == "code":
                res += _selective_escape(txt[prev:start]) + f"`{ent_text}`"
            elif ent.type == "text_link":
                res += (
                    _selective_escape(txt[prev:start])
                    + f"[{ent_text}]({ent.url})"
                )
            end += 1
        else:
            continue

        prev = end

    res += _selective_escape(txt[prev:])
    return res


def _escape_md(text: str) -> str:
    """Escape all Markdown special characters in ``text``."""
    for char in r"\*_`[]()~>#+-=|{}.!":
        text = text.replace(char, f"\\{char}")
    return text


# ---------------------------------------------------------------------------
# Button Markdown parser
# ---------------------------------------------------------------------------

def button_markdown_parser(
    txt: str,
    entities: Optional[Dict[MessageEntity, str]] = None,
    offset: int = 0,
) -> Tuple[str, List[Tuple[str, str, bool]]]:
    """
    Parse ``txt`` for ``[label](buttonurl:url)`` syntax, extracting inline
    keyboard button definitions and returning the cleaned text alongside them.

    Returns a tuple of:
    - ``note_data``: the text with button syntax removed
    - ``buttons``: list of ``(label, url, same_line)`` tuples

    The ``:same`` suffix on a button URL places it on the same keyboard row
    as the preceding button.
    """
    markdown_note: str = markdown_parser(txt, entities, offset)
    prev: int = 0
    note_data: str = ""
    buttons: List[Tuple[str, str, bool]] = []

    for match in _BTN_URL_REGEX.finditer(markdown_note):
        # Count backslash escapes immediately before the match.
        n_escapes: int = 0
        check: int = match.start(1) - 1
        while check >= 0 and markdown_note[check] == "\\":
            n_escapes += 1
            check -= 1

        if n_escapes % 2 == 0:
            # Even number of escapes → not escaped → create button.
            buttons.append((match.group(2), match.group(3), bool(match.group(4))))
            note_data += markdown_note[prev: match.start(1)]
            prev = match.end(1)
        else:
            # Odd number → escaped → include literally.
            note_data += markdown_note[prev: match.start(1) - 1]
            prev = match.start(1) - 1

    note_data += markdown_note[prev:]
    return note_data, buttons


# ---------------------------------------------------------------------------
# Curly bracket escaping for welcome templates
# ---------------------------------------------------------------------------

def escape_invalid_curly_brackets(text: str, valids: List[str]) -> str:
    """
    Escape ``{`` and ``}`` characters in ``text`` that are not part of a
    valid placeholder from ``valids``.

    Valid placeholders (e.g. ``{first}``) are left untouched; stray braces
    are doubled (``{{`` / ``}}``) so Python's ``str.format()`` does not raise.

    Example::

        escape_invalid_curly_brackets("Hello {first}! {bad}", ["first"])
        # → "Hello {first}! {{bad}}"
    """
    new_text: str = ""
    idx: int = 0
    while idx < len(text):
        ch: str = text[idx]
        if ch == "{":
            if idx + 1 < len(text) and text[idx + 1] == "{":
                new_text += "{{{{"
                idx += 2
                continue
            # Check if this starts a valid placeholder.
            matched: bool = False
            for v in valids:
                placeholder: str = "{" + v + "}"
                if text[idx:].startswith(placeholder):
                    new_text += placeholder
                    idx += len(placeholder)
                    matched = True
                    break
            if not matched:
                new_text += "{{"
                idx += 1
        elif ch == "}":
            if idx + 1 < len(text) and text[idx + 1] == "}":
                new_text += "}}}}"
                idx += 2
                continue
            new_text += "}}"
            idx += 1
        else:
            new_text += ch
            idx += 1

    return new_text


# ---------------------------------------------------------------------------
# Inline keyboard builder (shared by notes, filters, welcome)
# ---------------------------------------------------------------------------

def build_keyboard(
    buttons: List[Tuple[str, str, bool]],
) -> List[List[Dict[str, str]]]:
    """
    Convert a list of ``(label, url, same_line)`` tuples into a nested list
    suitable for constructing an ``InlineKeyboardMarkup``.

    ``same_line=True`` appends the button to the current row; ``False`` starts
    a new row.

    Returns a list of rows, each row being a list of ``{"text": ..., "url": ...}``
    dicts — callers must wrap with ``InlineKeyboardMarkup`` and
    ``InlineKeyboardButton``.
    """
    keyb: List[List[Dict[str, str]]] = []
    for label, url, same_line in buttons:
        if same_line and keyb:
            keyb[-1].append({"text": label, "url": url})
        else:
            keyb.append([{"text": label, "url": url}])
    return keyb


def revert_buttons(buttons: List[Tuple[str, str, bool]]) -> str:
    """
    Convert a list of button tuples back into ``[label](buttonurl:url)`` syntax
    so administrators can copy and edit the raw note/filter text.
    """
    parts: List[str] = []
    for label, url, same_line in buttons:
        suffix: str = ":same" if same_line else ""
        parts.append(f"\n[{label}](buttonurl:{url}{suffix})")
    return "".join(parts)
