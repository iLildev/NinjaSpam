"""
plugins/economy.py — High-level shim that loads the economy system from the economy/ package.

All logic resides in:
  tgbot/economy/models.py   — DB Models
  tgbot/economy/helpers.py  — Helper functions
  tgbot/economy/plugin.py   — Command handlers
"""

from economy.plugin import register  # noqa: F401
