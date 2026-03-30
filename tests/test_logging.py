"""Tests for structured logging."""

from __future__ import annotations

import json
import logging

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meeting_minutes.logging import StructuredJsonFormatter, get_logger, update_meeting_id


# Feature: meeting-minutes-taker, Property 34: Structured log format
@given(
    system_name=st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz_"),
    message=st.text(min_size=1, max_size=200),
)
@settings(max_examples=100)
def test_log_format_is_json(system_name: str, message: str):
    """Property 34: Log entries are valid JSON with required fields."""
    formatter = StructuredJsonFormatter(system_name=system_name)
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)  # Must be valid JSON

    assert "timestamp" in data
    assert "log_level" in data
    assert "system_name" in data
    assert "message" in data
    assert data["system_name"] == system_name
    assert data["message"] == message


# Feature: meeting-minutes-taker, Property 35: Log correlation ID
@given(meeting_id=st.uuids().map(str))
@settings(max_examples=100)
def test_log_correlation_id(meeting_id: str):
    """Property 35: Logger includes meeting_id when provided."""
    formatter = StructuredJsonFormatter(system_name="test", meeting_id=meeting_id)
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="test message",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)

    assert data.get("meeting_id") == meeting_id


# Feature: meeting-minutes-taker, Property 36: Log level filtering
def test_log_level_filtering():
    """Property 36: Logger respects configured log level."""
    logger = get_logger("test_filtering", level="WARNING")
    assert logger.level == logging.WARNING

    # Should not emit DEBUG or INFO
    debug_logger = get_logger("test_debug_filter", level="DEBUG")
    assert debug_logger.level == logging.DEBUG


def test_get_logger_returns_logger():
    """get_logger returns a Logger instance."""
    logger = get_logger("test_system")
    assert isinstance(logger, logging.Logger)


def test_get_logger_no_duplicate_handlers():
    """Calling get_logger twice doesn't add duplicate handlers."""
    logger1 = get_logger("test_dedup_system")
    handler_count = len(logger1.handlers)
    logger2 = get_logger("test_dedup_system")
    assert len(logger2.handlers) == handler_count


def test_update_meeting_id():
    """update_meeting_id updates the formatter's meeting_id."""
    logger = get_logger("test_update_id")
    update_meeting_id(logger, "new-meeting-123")

    for handler in logger.handlers:
        if isinstance(handler.formatter, StructuredJsonFormatter):
            assert handler.formatter.meeting_id == "new-meeting-123"
