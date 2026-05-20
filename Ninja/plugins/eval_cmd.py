"""
plugins/eval_cmd.py — Owner-only Python eval/exec commands.

Commands (OWNER only):
  /eval <code>  — Evaluate a Python expression.
  /exec <code>  — Execute Python statements.
  /sh   <cmd>   — Run a shell command.

⚠  These are highly privileged — only OWNER_IDs can use them.
"""

from __future__ import annotations

import io
import logging
import subprocess
import textwrap
import traceback
from contextlib import redirect_stdout
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from config import OWNER_IDS

logger = logging.getLogger(__name__)

_namespaces: dict[int, dict[str, Any]] = {}


def _namespace_of(chat_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict:
    if chat_id not in _namespaces:
        _namespaces[chat_id] = {
            "__builtins__": __builtins__,
            "context": context,
            "update": update,
            "bot": context.bot,
            "effective_message": update.effective_message,
            "effective_user": update.effective_user,
            "effective_chat": update.effective_chat,
        }
    return _namespaces[chat_id]


def _cleanup_code(code: str) -> str:
    if code.startswith("```") and code.endswith("```"):
        return "\n".join(code.split("\n")[1:-1])
    return code.strip("` \n")


async def _send_result(update: Update, context: ContextTypes.DEFAULT_TYPE, result: str) -> None:
    if not result:
        result = "Done (no output)."
    if len(result) > 3800:
        with io.BytesIO(result.encode()) as f:
            f.name = "output.txt"
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename="output.txt",
            )
    else:
        await update.effective_message.reply_text(
            f"<code>{result}</code>", parse_mode=ParseMode.HTML
        )


async def evaluate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.effective_message.text.split(" ", 1)
    if len(raw) < 2:
        await update.effective_message.reply_text("No code provided.")
        return
    body = _cleanup_code(raw[1])
    env = _namespace_of(update.effective_chat.id, update, context)
    stdout = io.StringIO()
    to_compile = f"def _func():\n{textwrap.indent(body, '  ')}"
    try:
        exec(to_compile, env)
    except Exception as e:
        await _send_result(update, context, f"{e.__class__.__name__}: {e}")
        return
    try:
        with redirect_stdout(stdout):
            func_return = env["_func"]()
    except Exception:
        value = stdout.getvalue()
        await _send_result(update, context, f"{value}{traceback.format_exc()}")
        return
    value = stdout.getvalue()
    if func_return is None:
        if value:
            result = value
        else:
            try:
                result = repr(eval(body, env))
            except Exception:
                result = value or "Done."
    else:
        result = f"{value}{func_return}"
    await _send_result(update, context, result)


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.effective_message.text.split(" ", 1)
    if len(raw) < 2:
        await update.effective_message.reply_text("No code provided.")
        return
    body = _cleanup_code(raw[1])
    env = _namespace_of(update.effective_chat.id, update, context)
    stdout = io.StringIO()
    try:
        with redirect_stdout(stdout):
            exec(body, env)
        result = stdout.getvalue() or "Done."
    except Exception:
        result = f"{stdout.getvalue()}{traceback.format_exc()}"
    await _send_result(update, context, result)


async def shell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.effective_message.text.split(" ", 1)
    if len(raw) < 2:
        await update.effective_message.reply_text("No command provided.")
        return
    cmd = raw[1]
    try:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(timeout=30)
        reply = ""
        if stdout:
            reply += f"<b>Stdout:</b>\n<code>{stdout.decode()}</code>\n"
        if stderr:
            reply += f"<b>Stderr:</b>\n<code>{stderr.decode()}</code>"
        if not reply:
            reply = "Done (no output)."
    except subprocess.TimeoutExpired:
        reply = "Command timed out."
    except Exception as e:
        reply = f"Error: {e}"

    if len(reply) > 3800:
        with io.BytesIO(reply.encode()) as f:
            f.name = "shell_output.txt"
            await context.bot.send_document(
                chat_id=update.effective_chat.id, document=f, filename="shell_output.txt"
            )
    else:
        await update.effective_message.reply_text(reply, parse_mode=ParseMode.HTML)


async def clear_locals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    _namespaces.pop(chat_id, None)
    await update.effective_message.reply_text("Cleared local namespace.")


async def register(application: Application) -> None:
    owner_filter = filters.User(user_id=list(OWNER_IDS))
    application.add_handler(CommandHandler(["eval", "ev", "e"], evaluate, filters=owner_filter))
    application.add_handler(CommandHandler(["exec", "py", "x"], execute, filters=owner_filter))
    application.add_handler(CommandHandler("sh", shell, filters=owner_filter))
    application.add_handler(CommandHandler("clearlocals", clear_locals, filters=owner_filter))
