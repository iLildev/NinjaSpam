"""
plugins/__init__.py — Plugin directory marker.

Every module placed in this directory is automatically discovered and loaded
by the Dynamic Plugin Loader (core/plugin_loader.py, built in Task 7).

Plugin authoring contract:
--------------------------
Each plugin module MUST expose a top-level async function with the signature:

    async def register(application: telegram.ext.Application) -> None:
        \"\"\"Register all handlers this plugin provides.\"\"\"
        ...

The plugin loader calls ``register(application)`` for every discovered module.
If the function is absent, the loader logs a warning and skips the module
without raising — satisfying the Resilience requirement.

Plugins MUST NOT import directly from other plugins.  Shared utilities belong
in ``core/`` or a dedicated ``lib/`` sub-package.
"""

# This file is intentionally empty for Task 1.
# It marks 'plugins/' as a Python package so that the dynamic loader can
# use importlib to import modules from this directory by package path.
