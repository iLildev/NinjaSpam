"""
core/helpers/__init__.py — Public re-exports for the helpers sub-package.

Import from here instead of sub-modules so internal refactors do not
break plugin import paths.
"""

from core.helpers.chat_status import (
    bot_admin,
    can_delete,
    can_pin,
    can_promote,
    can_restrict,
    is_bot_admin,
    is_user_admin,
    is_user_ban_protected,
    is_user_in_chat,
    user_admin,
    user_admin_no_reply,
    user_not_admin,
)
from core.helpers.extraction import extract_user, extract_user_and_text
from core.helpers.string_handling import (
    button_markdown_parser,
    escape_invalid_curly_brackets,
    extract_time,
    markdown_parser,
    split_quotes,
)

__all__: list[str] = [
    # chat_status
    "bot_admin",
    "can_delete",
    "can_pin",
    "can_promote",
    "can_restrict",
    "is_bot_admin",
    "is_user_admin",
    "is_user_ban_protected",
    "is_user_in_chat",
    "user_admin",
    "user_admin_no_reply",
    "user_not_admin",
    # extraction
    "extract_user",
    "extract_user_and_text",
    # string_handling
    "button_markdown_parser",
    "escape_invalid_curly_brackets",
    "extract_time",
    "markdown_parser",
    "split_quotes",
]
