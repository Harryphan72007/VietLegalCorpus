"""Standard-library logging setup (structured formatter, no third-party dep)."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Install a single stderr handler at ``level``; safe to call more than once."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
