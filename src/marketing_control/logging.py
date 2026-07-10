"""Local application logging with best-effort credential redaction."""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from marketing_control.settings import Settings

_SENSITIVE_VALUE = re.compile(
    r"""(?ix)
    (?P<key>
        (?:client[ _-]?)?secret | password | (?:oauth[ _-]?)?token |
        developer[ _-]?token | credential | authorization | api[ _-]?key
    )
    (?P<key_quote>[\"']?)
    (?P<separator>\s*(?:=|:)\s*|\s+)
    (?P<value>\"(?:\\\\.|[^\"\\\\])*\"|'(?:\\\\.|[^'\\\\])*'|(?:Bearer\s+)?[^\s,;]+)
    """
)


def redact_sensitive_values(value: str) -> str:
    """Replace common credential values in text with a fixed marker."""
    return _SENSITIVE_VALUE.sub(
        lambda match: (
            f"{match.group('key')}{match.group('key_quote')}"
            f"{match.group('separator')}[REDACTED]"
        ),
        value,
    )


class RedactingFormatter(logging.Formatter):
    """Apply best-effort credential redaction to messages and tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        record_copy = logging.makeLogRecord(record.__dict__.copy())
        record_copy.msg = redact_sensitive_values(record.getMessage())
        record_copy.args = ()
        return super().format(record_copy)

    def formatException(self, exc_info: logging._SysExcInfoType) -> str:
        return redact_sensitive_values(super().formatException(exc_info))


class _SecureRotatingFileHandler(RotatingFileHandler):
    """Create log files with owner-only permissions on supported platforms."""

    def _open(self) -> Any:
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream


def configure_logging(
    settings: Settings, *, logger_name: str = "marketing_control"
) -> logging.Logger:
    """Configure a rotating file logger using the paths from ``settings``."""
    log_directory = settings.paths.logs
    log_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(log_directory, 0o700)

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    handler = _SecureRotatingFileHandler(
        Path(log_directory) / "marketing-control.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    return logger
