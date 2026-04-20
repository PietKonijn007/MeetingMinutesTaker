"""Tests for desktop notifications (NOT-1)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from meeting_minutes.config import NotificationsConfig
from meeting_minutes import notifications as notif


def _mock_pync(monkeypatch):
    """Install a fake ``pync`` module that records notify() calls."""
    calls: list[dict] = []

    class _FakeNotifier:
        @staticmethod
        def notify(message, **kwargs):
            calls.append({"message": message, **kwargs})

    fake_pync = SimpleNamespace(Notifier=_FakeNotifier)
    monkeypatch.setattr(notif, "_get_pync", lambda: fake_pync)
    return calls


def test_complete_fires_with_title_and_body(monkeypatch):
    calls = _mock_pync(monkeypatch)
    cfg = NotificationsConfig(enabled=True, sound=True)

    ok = notif.notify_pipeline_complete(
        meeting_id="m-abc",
        title="1:1 with Jon",
        duration="30 minutes",
        action_count=2,
        config=cfg,
    )
    assert ok is True
    assert len(calls) == 1
    call = calls[0]
    assert call["title"] == "Meeting ready: 1:1 with Jon"
    assert "30 minutes" in call["message"]
    assert "2 action items" in call["message"]
    assert call["open"].endswith("/m-abc")
    assert call["sound"] == "default"


def test_complete_singular_action(monkeypatch):
    calls = _mock_pync(monkeypatch)
    cfg = NotificationsConfig(enabled=True, sound=False)

    notif.notify_pipeline_complete(
        meeting_id="m-xyz", title="Standup", duration=None, action_count=1,
        config=cfg,
    )
    assert "1 action item" in calls[0]["message"]
    assert "sound" not in calls[0]  # disabled


def test_failed_fires_with_stage_and_error(monkeypatch):
    calls = _mock_pync(monkeypatch)
    cfg = NotificationsConfig(enabled=True)

    ok = notif.notify_pipeline_failed(
        meeting_id="m-fail",
        title="Planning",
        stage="generate",
        error="LLM timeout after 120s",
        config=cfg,
    )
    assert ok is True
    assert "Pipeline failed" in calls[0]["title"]
    assert "Planning" in calls[0]["title"]
    assert "generate" in calls[0]["message"]
    assert "LLM timeout" in calls[0]["message"]


def test_failed_truncates_long_error(monkeypatch):
    calls = _mock_pync(monkeypatch)
    cfg = NotificationsConfig(enabled=True)

    notif.notify_pipeline_failed(
        meeting_id="m", title="T", stage="ingest", error="x" * 500, config=cfg,
    )
    # Total body ≈ stage + short_error (≤ 160 chars + ellipsis)
    assert len(calls[0]["message"]) <= 200
    assert calls[0]["message"].endswith("…")


def test_disabled_short_circuits(monkeypatch):
    calls = _mock_pync(monkeypatch)
    cfg = NotificationsConfig(enabled=False)

    ok = notif.notify_pipeline_complete(
        meeting_id="m", title="T", config=cfg,
    )
    assert ok is False
    assert calls == []


def test_non_macos_is_noop(monkeypatch):
    """Reaching the platform check with pync unavailable returns False."""
    monkeypatch.setattr(notif, "sys", SimpleNamespace(platform="linux"))
    # _get_pync consults real sys.platform via the module-level import;
    # simulate by monkeypatching sys.platform for the scope of this test.
    monkeypatch.setattr(sys, "platform", "linux")

    cfg = NotificationsConfig(enabled=True)  # user forced it on
    ok = notif.notify_pipeline_complete(
        meeting_id="m", title="T", config=cfg,
    )
    assert ok is False


def test_pync_missing_is_noop(monkeypatch):
    """When pync is not installed on macOS, _get_pync() returns None."""
    monkeypatch.setattr(sys, "platform", "darwin")

    # Shadow the real import with an ImportError. We need to unset the
    # module-level cache flag so the INFO log fires in a clean state.
    monkeypatch.setattr(notif, "_pync_missing_logged", False)

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "pync":
            raise ImportError("No module named 'pync'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    cfg = NotificationsConfig(enabled=True)
    ok = notif.notify_pipeline_complete(meeting_id="m", title="T", config=cfg)
    assert ok is False


def test_notifications_config_platform_default(monkeypatch):
    """NotificationsConfig defaults enabled=True on darwin, False elsewhere."""
    monkeypatch.setattr(sys, "platform", "darwin")
    cfg = NotificationsConfig()
    assert cfg.enabled is True

    monkeypatch.setattr(sys, "platform", "linux")
    cfg2 = NotificationsConfig()
    assert cfg2.enabled is False


def test_complete_click_url_uses_config_base(monkeypatch):
    calls = _mock_pync(monkeypatch)
    cfg = NotificationsConfig(
        enabled=True, click_url_base="http://localhost:9999/m/",
    )
    notif.notify_pipeline_complete(meeting_id="abc", title="T", config=cfg)
    assert calls[0]["open"] == "http://localhost:9999/m/abc"
