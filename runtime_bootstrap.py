"""Early stdio redirection for silent Windows launches.

``pythonw.exe`` (used by ``start_hidden.vbs``) has no console, so
``sys.stdout``/``sys.stderr`` are ``None``. If the very first import fails —
a missing dependency, a broken ``.env`` or a syntax error — the traceback would
disappear. This module has no project imports on purpose: it can run before
anything else and append the traceback to ``logs/worker.log`` (or
``logs/supervisor.log``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def logs_dir() -> Path:
    override = os.environ.get("MARKETPLACE_LOG_DIR")
    return Path(override) if override else BASE_DIR / "logs"


def redirect_stdio(filename: str) -> Path | None:
    """Point missing stdout/stderr at ``logs/<filename>``; return the log path."""
    directory = logs_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    path = directory / filename
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(path, "a", encoding="utf-8", buffering=1))
            except OSError:
                return None
    return path


def install_excepthook(filename: str) -> None:
    """Log uncaught exceptions (including import-time ones) to the same file."""
    import traceback

    path = redirect_stdio(filename)
    if path is None:
        return

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        try:
            with open(path, "a", encoding="utf-8") as stream:
                stream.write("\n=== Необработанная ошибка ===\n")
                traceback.print_exception(exc_type, exc, tb, file=stream)
                stream.flush()
        except OSError:
            pass

    sys.excepthook = _hook
