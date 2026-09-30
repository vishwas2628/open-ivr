"""Logging setup: non-blocking queue logging, rotating file + colour console."""

from __future__ import annotations

import logging
import logging.handlers
import queue as queue_mod
import sys
from typing import Any

from .config import Config

_listener: logging.handlers.QueueListener | None = None
_log_queue: queue_mod.SimpleQueue | None = None

RESET = "\033[0m"
COLORS = {
    "DEBUG": "\033[38;5;244m",
    "INFO": "\033[38;5;39m",
    "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;203m",
    "CRITICAL": "\033[1;38;5;196m",
}
LEVEL_COLORS = {logging.ERROR: COLORS["ERROR"], logging.WARNING: COLORS["WARNING"]}


class ColorFormatter(logging.Formatter):
    """Formatter that colourises the level name and dims the location."""

    def __init__(self, fmt: str, datefmt: str, use_color: bool = True) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.use_color = use_color and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        if not self.use_color:
            return super().format(record)
        plain = logging.Formatter(self._style._fmt, self.datefmt).format(record)
        color = COLORS.get(record.levelname, "")
        if not color:
            return plain
        head, sep, rest = plain.partition(record.levelname)
        if not sep:
            return plain
        return f"{head}{color}{record.levelname}{RESET}{rest}"


def _resolve_level(level: str) -> int:
    if isinstance(level, int):
        return level
    return logging.getLevelNamesMapping().get(str(level).upper(), logging.INFO)


def setup_logging(cfg: Config, **_: Any) -> logging.Logger:
    """Configure the root logger; safe to call more than once.

    Every ``log`` call returns immediately: the record is handed to a
    :class:`queue.SimpleQueue` and a listener thread does the actual formatting
    and file I/O, so a slow disk can never stall the ARI event loop.
    """
    global _listener, _log_queue

    log_cfg = cfg.logging
    level = _resolve_level(log_cfg.level)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    if _listener is not None:
        _listener.stop()
        _listener = None

    root.setLevel(level)
    root.propagate = False

    handlers: list[logging.Handler] = []

    if log_cfg.console:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(level)
        console.setFormatter(
            ColorFormatter(log_cfg.format, log_cfg.datefmt, use_color=log_cfg.color)
        )
        handlers.append(console)

    log_file = cfg.path(log_cfg.file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    rotating = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=int(log_cfg.max_bytes),
        backupCount=int(log_cfg.backup_count),
        encoding="utf-8",
    )
    rotating.setLevel(level)
    rotating.setFormatter(logging.Formatter(log_cfg.format, log_cfg.datefmt))
    handlers.append(rotating)

    _log_queue = queue_mod.SimpleQueue()
    root.addHandler(logging.handlers.QueueHandler(_log_queue))

    _listener = logging.handlers.QueueListener(_log_queue, *handlers, respect_handler_level=True)
    _listener.start()

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("asyncari").setLevel(logging.INFO)

    return logging.getLogger("openivr")


def shutdown_logging() -> None:
    """Drain the queue and stop the listener thread (call on exit)."""
    global _listener, _log_queue
    if _listener is not None:
        _listener.stop()
        _listener = None
    for handler in list(logging.getLogger().handlers):
        handler.flush()
    _log_queue = None
