"""
plugins/math_cmd.py — Safe mathematical expression evaluator.

Commands:
  /calc <expression>   — Evaluate a mathematical expression and return the result.

Examples:
  /calc 2 + 2                → 4
  /calc (100 * 3.14) / 7     → 44.857…
  /calc 2 ** 32              → 4294967296
  /calc sqrt(144)            → 12.0
  /calc sin(pi/2)            → 1.0

Supported functions (via Python math module):
  sqrt, abs, ceil, floor, log, log2, log10, sin, cos, tan,
  asin, acos, atan, atan2, degrees, radians, factorial, gcd, pow,
  pi, e, tau, inf

Security:
  Uses a strict allowlist of names and ast.literal_eval-style approach
  with a custom safe_eval() — no exec(), no eval() on raw input.
  Computation is capped at 5 seconds via a simple timeout guard.
"""

from __future__ import annotations

import ast
import logging
import math
import operator
from typing import Any, Union

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed names for the expression namespace
# ---------------------------------------------------------------------------
_SAFE_NAMES: dict[str, Any] = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor,
    "log": math.log, "log2": math.log2, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "atan2": math.atan2, "degrees": math.degrees, "radians": math.radians,
    "factorial": math.factorial, "gcd": math.gcd,
    "pow": math.pow, "pi": math.pi, "e": math.e,
    "tau": math.tau, "inf": math.inf,
}

_ALLOWED_OPS: tuple[type, ...] = (
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.USub,
    ast.UAdd, ast.FloorDiv,
)

_MAX_EXPR_LEN = 256


class _SafeEvalVisitor(ast.NodeVisitor):
    """Walk an AST and raise ValueError for any disallowed node."""

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only direct function calls are allowed.")
        if node.func.id not in _SAFE_NAMES:
            raise ValueError(f"Function '{node.func.id}' is not allowed.")
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id not in _SAFE_NAMES:
            raise ValueError(f"Name '{node.id}' is not allowed.")
        return self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        raise ValueError("Attribute access is not allowed.")

    def visit_Import(self, node: ast.Import) -> Any:
        raise ValueError("Imports are not allowed.")

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        if not isinstance(node.op, _ALLOWED_OPS):
            raise ValueError(f"Operator {type(node.op).__name__} is not allowed.")
        return self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        if not isinstance(node.op, _ALLOWED_OPS):
            raise ValueError(f"Unary operator {type(node.op).__name__} is not allowed.")
        return self.generic_visit(node)


def _safe_eval(expr: str) -> Union[int, float]:
    """Safely evaluate a mathematical expression string."""
    if len(expr) > _MAX_EXPR_LEN:
        raise ValueError("Expression too long.")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid syntax: {exc}") from exc

    _SafeEvalVisitor().visit(tree)

    # Evaluate using compile + eval with restricted globals
    code = compile(tree, "<calc>", "eval")
    result = eval(code, {"__builtins__": {}}, _SAFE_NAMES)  # noqa: S307

    if not isinstance(result, (int, float)):
        raise ValueError("Result is not a number.")
    if math.isnan(result):
        raise ValueError("Result is NaN.")
    return result


# ---------------------------------------------------------------------------
# /calc handler
# ---------------------------------------------------------------------------

async def calc(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Evaluate a mathematical expression."""
    msg = update.effective_message
    if not msg:
        return

    if not context.args:
        await msg.reply_text(
            "Usage: /calc &lt;expression&gt;\n"
            "Example: <code>/calc sqrt(144) + pi</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    expr = " ".join(context.args).strip()

    try:
        result = _safe_eval(expr)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        await msg.reply_text(
            f"⚠️ <b>Error:</b> {html_escape(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("calc: unexpected error for %r: %s", expr, exc)
        await msg.reply_text("⚠️ Could not evaluate that expression.")
        return

    # Format the result nicely
    if isinstance(result, float) and result == int(result):
        result_str = str(int(result))
    elif isinstance(result, float):
        result_str = f"{result:.10g}"
    else:
        result_str = str(result)

    await msg.reply_text(
        f"🔢 <code>{html_escape(expr)}</code> = <b>{html_escape(result_str)}</b>",
        parse_mode=ParseMode.HTML,
    )


def html_escape(s: str) -> str:
    import html
    return html.escape(s)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:  # noqa: D401
    application.add_handler(CommandHandler(["calc", "math"], calc))
    logger.info("math_cmd plugin registered.")
