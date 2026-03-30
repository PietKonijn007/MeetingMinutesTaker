"""Structured JSON logging for Meeting Minutes Taker."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class StructuredJsonFormatter(logging.Formatter):
    """Format log records as JSON with structured fields."""

    def __init__(self, system_name: str, meeting_id: str | None = None) -> None:
        super().__init__()
        self.system_name = system_name
        self.meeting_id = meeting_id

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "log_level": record.levelname,
            "system_name": self.system_name,
            "message": record.getMessage(),
        }
        if self.meeting_id:
            log_data["meeting_id"] = self.meeting_id

        # Include extra fields attached to the record
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
            }:
                log_data[key] = value

        if record.exc_info:
            log_data["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_data, default=str)


def get_logger(
    system_name: str, meeting_id: str | None = None, level: str = "INFO"
) -> logging.Logger:
    """Return a logger that emits structured JSON with correlation ID."""
    logger = logging.getLogger(f"meeting_minutes.{system_name}")

    # Avoid adding duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter(system_name, meeting_id))
        logger.addHandler(handler)

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    return logger


def update_meeting_id(logger: logging.Logger, meeting_id: str) -> None:
    """Update the meeting_id in the logger's formatter (for correlation)."""
    for handler in logger.handlers:
        if isinstance(handler.formatter, StructuredJsonFormatter):
            handler.formatter.meeting_id = meeting_id
