---
name: English-only enforcement
description: Which files still contain Arabic and why it's intentional
---

All user-facing strings across 125 source files translated to English.

## Files that legitimately keep Arabic characters

| File | Reason |
|------|--------|
| `core/fun_data.py` | Decorative ASCII art emoji strings (`(っ˘ڡ˘ς)`, `٩(╬ʘ益ʘ╬)۶`) |
| `core/helpers/city_timezones.py` | Arabic city-name dictionary keys for bilingual lookup |
| `core/helpers/fuzzy.py` | Arabic chars in regex patterns for Arabic text normalization |
| `quiz/plugin.py` | Arabic chars in regex patterns for answer normalization |
| `quiz/questions.py` | Arabic alternative accepted answers (user inputs, not displayed text) |

**Why:** These are functional data/code — not messages displayed to users. Removing them would break features (city lookup, Arabic answer normalization).

**How to apply:** When scanning for Arabic, use `-P '[\x{0600}-\x{06FF}]'` with ripgrep. Matches in the 5 files above are expected and correct.
