"""Structured logging per CLAUDE.md spec.

Format: `2026-05-01T14:32:01Z [INFO] [module] message`
ISO timestamp in UTC, level in brackets, module label in brackets.

NEVER log PII values. Modules log metadata only (counts, types, sizes).
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Optional

from .config import LOG_FILE, LOG_LEVEL


class _ZuluFormatter(logging.Formatter):
    """ISO-8601 UTC timestamp with `Z` suffix."""

    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        ct = self.converter(record.created)
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", ct)


_FORMAT = "%(asctime)s [%(levelname)s] [%(module_label)s] %(message)s"


class _ModuleLabelAdapter(logging.LoggerAdapter):
    """Wraps a logger to inject the module label into every record."""

    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})["module_label"] = self.extra["module_label"]
        return msg, kwargs


_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("docanon")
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    root.propagate = False

    # Avoid duplicates if reloaded.
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = _ZuluFormatter(_FORMAT)

    file_h = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_h.setFormatter(fmt)
    root.addHandler(file_h)

    stream_h = logging.StreamHandler(sys.stdout)
    stream_h.setFormatter(fmt)
    root.addHandler(stream_h)

    _configured = True


def get_logger(label: str) -> logging.LoggerAdapter:
    """Return an adapter that emits records with the given `[label]` tag."""
    _configure_root()
    base = logging.getLogger(f"docanon.{label}")
    return _ModuleLabelAdapter(base, {"module_label": label})
