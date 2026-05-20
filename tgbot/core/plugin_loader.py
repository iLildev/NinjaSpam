"""
core/plugin_loader.py — Dynamic plugin discovery and registration (Task 7).

Implements the drop-in plugin loader that:
1. Scans ``config.PLUGINS_DIR`` for ``*.py`` files.
2. Imports each as a module via ``importlib``.
3. Calls ``await module.register(application)`` if the function exists.
4. Logs each successful load; silently skips modules that fail (resilience).

Plugin contract:
    Every plugin must expose exactly one coroutine::

        async def register(application: Application) -> None:
            application.add_handler(...)

    If ``register`` is absent or raises, the loader emits a WARNING and moves
    on — a single broken plugin does not prevent the rest from loading.

Load order:
    Files are sorted alphabetically so the load order is deterministic and
    reproducible across restarts.  If a plugin depends on another being loaded
    first, prefix its filename with a numeric order key (e.g. ``00_gban.py``).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional

from telegram.ext import Application

import config

logger = logging.getLogger(__name__)


async def load_all_plugins(application: Application) -> int:
    """
    Discover and load all plugins from ``config.PLUGINS_DIR``.

    Returns the number of successfully loaded plugins.

    Args:
        application: The fully initialised PTB Application instance.

    Returns:
        Count of plugins that loaded without error.
    """
    plugins_dir: Path = config.PLUGINS_DIR

    if not plugins_dir.is_dir():
        logger.warning(
            "Plugins directory does not exist: %s — no plugins loaded.",
            plugins_dir,
        )
        return 0

    plugin_files = sorted(plugins_dir.glob("*.py"))
    # Skip private / dunder files like __init__.py.
    plugin_files = [p for p in plugin_files if not p.name.startswith("_")]

    loaded_count: int = 0

    for plugin_path in plugin_files:
        module_name: str = f"plugins.{plugin_path.stem}"
        module: Optional[ModuleType] = _import_plugin(plugin_path, module_name)

        if module is None:
            continue

        register_fn = getattr(module, "register", None)
        if register_fn is None:
            logger.warning(
                "Plugin '%s' does not expose a 'register' coroutine — skipping.",
                plugin_path.name,
            )
            continue

        if not _is_coroutine_function(register_fn):
            logger.warning(
                "Plugin '%s'.register is not a coroutine function — skipping.",
                plugin_path.name,
            )
            continue

        try:
            await register_fn(application)
            loaded_count += 1
            logger.debug("Plugin loaded: %s", plugin_path.stem)
        except Exception as exc:
            logger.warning(
                "Plugin '%s' raised an exception during register(): %s — skipping.",
                plugin_path.name,
                exc,
                exc_info=True,
            )

    logger.info(
        "Plugin loader complete: %d/%d plugins loaded successfully.",
        loaded_count,
        len(plugin_files),
    )
    return loaded_count


def _import_plugin(path: Path, module_name: str) -> Optional[ModuleType]:
    """
    Import a plugin module from a file path.

    Uses ``importlib.util.spec_from_file_location`` so the module is
    importable even when the plugins directory is not on ``sys.path``.

    Returns the module on success, or None on ImportError / SyntaxError.
    """
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.warning("Could not build module spec for %s.", path)
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
    except (ImportError, SyntaxError) as exc:
        logger.warning(
            "Failed to import plugin '%s': %s", path.name, exc, exc_info=True
        )
        return None
    except Exception as exc:
        logger.warning(
            "Unexpected error importing plugin '%s': %s", path.name, exc, exc_info=True
        )
        return None


def _is_coroutine_function(func: object) -> bool:
    """Return True if ``func`` is an async/coroutine function."""
    import asyncio
    import inspect
    return asyncio.iscoroutinefunction(func) or inspect.iscoroutinefunction(func)
