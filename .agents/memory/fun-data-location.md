---
name: fun_data location
description: fun_strings.py was moved to core/fun_data.py
---

`Ninja/plugins/fun_strings.py` → `Ninja/core/fun_data.py`

`Ninja/plugins/fun.py` imports: `from core import fun_data as fun_strings`

**Why:** plugin_loader emitted a WARNING at startup because fun_strings.py has no `register()` function. Moving it to core/ removes it from the plugin scan entirely.

**How to apply:** If fun-related data (GIFs, strings, templates) needs adding, edit `core/fun_data.py`.
