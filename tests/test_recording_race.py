"""Regression test for the PortAudio start/list race.

Background: the UI polls /api/audio-devices every 3 seconds while the
recording state is idle. That endpoint calls ``sd._terminate() + sd._initialize()``
under ``_audio_lock`` so it can pick up newly-plugged Bluetooth/USB devices.

Before the fix, ``POST /api/recording/start`` opened its PortAudio
``InputStream`` WITHOUT acquiring ``_audio_lock``. If the user clicked Start
during the small window when the poll was mid-terminate, PortAudio returned
"Error querying device -1" or "Could not obtain stream info" and the user
got a "Failed to start recording" toast. That's exactly the "cannot record
after stopping the previous meeting" report.

This test asserts that start_recording now takes ``_audio_lock`` before
constructing / starting the AudioCaptureEngine. It does it by holding the
lock from one thread and verifying the endpoint can't complete until the
lock is released.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_minutes.api.deps import get_config
from meeting_minutes.api.routes import recording as rec_module
from meeting_minutes.config import AppConfig


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(rec_module.router)
    app.dependency_overrides[get_config] = lambda: AppConfig()
    return app


@pytest.fixture
def client(app, monkeypatch, tmp_path):
    """TestClient with AudioCaptureEngine stubbed so tests don't touch PortAudio."""

    class _FakeEngine:
        def __init__(self, *a, **kw):
            self._meeting_id = "meeting-abc"
            self._recording = False

        def start(self):
            # Simulate a short PortAudio open — this is what used to race.
            time.sleep(0.02)
            self._recording = True
            return self._meeting_id

        def is_recording(self):
            return self._recording

        def stop(self):
            self._recording = False
            res = MagicMock()
            res.audio_file = str(tmp_path / "fake.flac")
            res.duration_seconds = 1.0
            return res

    # Patch the lazy import inside start_recording.
    import meeting_minutes.system1.capture as capture_module
    monkeypatch.setattr(capture_module, "AudioCaptureEngine", _FakeEngine)

    # Reset the module-level state between tests.
    rec_module._current_recording.update({
        "state": "idle", "meeting_id": None, "start_time": None,
        "engine": None, "language": None,
    })

    return TestClient(app)


def test_start_recording_waits_for_audio_lock(client):
    """If _audio_lock is held, start_recording must block until it's released.

    Before the fix: start_recording would sail through without acquiring the
    lock and crash at sd.InputStream(). Now it acquires the lock, so the
    endpoint blocks cleanly while a concurrent device-list refresh finishes.
    """
    rec_module._audio_lock.acquire()
    try:
        completed = threading.Event()
        result: dict = {}

        def call_start():
            resp = client.post("/api/recording/start", json={})
            result["status"] = resp.status_code
            result["body"] = resp.json() if resp.content else None
            completed.set()

        t = threading.Thread(target=call_start, daemon=True)
        t.start()

        # While the lock is held, the endpoint must not complete. If it does,
        # the fix is missing and the old race path is live.
        assert not completed.wait(timeout=0.5), (
            "start_recording returned while _audio_lock was held — this "
            "means the endpoint is not acquiring the lock and can race "
            "with /api/audio-devices (which calls sd._terminate()). See "
            "the PortAudio start/list race regression PR."
        )
    finally:
        rec_module._audio_lock.release()

    # After the lock is released, the endpoint should complete promptly.
    assert completed.wait(timeout=5), "start_recording never completed after lock release"
    assert result["status"] == 200, result


def test_list_audio_devices_default_never_terminates(client, monkeypatch):
    """The default (`GET /api/audio-devices`) must NEVER call sd._terminate().

    The UI polls this endpoint every 3 s. If the poll terminates PortAudio
    while a stream is live, the recording goes silent (FLAC is empty,
    elapsed counts up, nothing gets captured). The re-scan is now opt-in
    via `?refresh=true` so the periodic poll is harmless. Tested both
    with and without an active recording — neither case should terminate.
    """
    terminate_calls = []
    fake_sd = type("sd", (), {
        "_terminate": lambda: terminate_calls.append(1),
        "_initialize": lambda: None,
        "query_devices": lambda: [],
    })
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    # Default path, no recording active.
    for _ in range(5):
        assert client.get("/api/audio-devices").status_code == 200
    assert terminate_calls == [], "default poll terminated PortAudio while idle"

    # Default path with an active recording.
    assert client.post("/api/recording/start", json={}).status_code == 200
    for _ in range(10):
        assert client.get("/api/audio-devices").status_code == 200

    assert terminate_calls == [], (
        f"sd._terminate() was called {len(terminate_calls)} times during the "
        "default poll — this silently kills any live stream. The re-scan "
        "must remain opt-in via ?refresh=true."
    )


def test_list_audio_devices_refresh_triggers_rescan_when_idle(client, monkeypatch):
    """The Refresh button (?refresh=true) must call sd._terminate() when
    nothing is recording, so newly-plugged Bluetooth/USB devices are picked
    up. And it must NOT call it when a recording is active — crashing the
    stream during a manual refresh is still a crash."""
    terminate_calls = []
    fake_sd = type("sd", (), {
        "_terminate": lambda: terminate_calls.append(1),
        "_initialize": lambda: None,
        "query_devices": lambda: [],
    })
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

    # Idle: refresh=true SHOULD call _terminate.
    assert client.get("/api/audio-devices?refresh=true").status_code == 200
    assert len(terminate_calls) == 1, "refresh=true did not force a PortAudio re-scan"

    # While recording: refresh=true must STILL not terminate.
    assert client.post("/api/recording/start", json={}).status_code == 200
    assert client.get("/api/audio-devices?refresh=true").status_code == 200
    assert len(terminate_calls) == 1, (
        "refresh=true terminated PortAudio while recording — this would crash "
        "the live stream. The state check must gate the terminate."
    )


def test_start_recording_succeeds_under_concurrent_device_list(client):
    """Simulate the exact user scenario: a background thread hammers the
    device-list endpoint while the user clicks Start. All starts must
    succeed (no PortAudio race-induced 500s)."""
    stop = threading.Event()
    poll_count = [0]

    def poll():
        while not stop.is_set():
            # Must be safe even under the lock, since list_audio_devices
            # acquires it too.
            try:
                with rec_module._audio_lock:
                    # Simulate the sd._terminate() + sd._initialize() window.
                    time.sleep(0.01)
            except Exception:
                pass
            poll_count[0] += 1

    t = threading.Thread(target=poll, daemon=True)
    t.start()

    try:
        failures = []
        for i in range(10):
            resp = client.post("/api/recording/start", json={})
            if resp.status_code != 200:
                failures.append((i, resp.status_code, resp.text[:100]))
                break
            # Reset state so next start isn't blocked by "Already recording".
            rec_module._current_recording.update({
                "state": "idle", "meeting_id": None, "start_time": None,
                "engine": None, "language": None,
            })

        assert not failures, f"concurrent-poll stress failed: {failures}"
    finally:
        stop.set()
        t.join(timeout=2)

    assert poll_count[0] > 0, "the poll thread didn't actually run — test harness broken"
