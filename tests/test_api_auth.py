"""Tests for API key authentication middleware (SEC-1)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from meeting_minutes.api.auth import ApiKeyMiddleware


def _make_app(api_key: str = "") -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiKeyMiddleware, api_key=api_key)

    @app.get("/api/meetings")
    def meetings():
        return {"meetings": []}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/health/full")
    def health_full():
        return {"status": "ok", "checks": []}

    @app.get("/other")
    def other():
        return {"page": "home"}

    return app


class TestNoKeyConfigured:
    """When api_key is empty, all requests pass through (backward compat)."""

    def test_api_endpoint_allowed(self):
        client = TestClient(_make_app(""))
        resp = client.get("/api/meetings")
        assert resp.status_code == 200

    def test_health_allowed(self):
        client = TestClient(_make_app(""))
        resp = client.get("/api/health")
        assert resp.status_code == 200


class TestKeyConfigured:
    """When api_key is set, /api/* requires the correct X-Api-Key header."""

    KEY = "test-secret-key-12345"

    def test_missing_key_rejected(self):
        client = TestClient(_make_app(self.KEY))
        resp = client.get("/api/meetings")
        assert resp.status_code == 401
        assert "Invalid or missing API key" in resp.json()["detail"]

    def test_wrong_key_rejected(self):
        client = TestClient(_make_app(self.KEY))
        resp = client.get("/api/meetings", headers={"X-Api-Key": "wrong"})
        assert resp.status_code == 401

    def test_correct_key_allowed(self):
        client = TestClient(_make_app(self.KEY))
        resp = client.get("/api/meetings", headers={"X-Api-Key": self.KEY})
        assert resp.status_code == 200

    def test_health_exempt(self):
        client = TestClient(_make_app(self.KEY))
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_health_full_exempt(self):
        client = TestClient(_make_app(self.KEY))
        resp = client.get("/api/health/full")
        assert resp.status_code == 200

    def test_non_api_path_exempt(self):
        client = TestClient(_make_app(self.KEY))
        resp = client.get("/other")
        assert resp.status_code == 200


class TestRecordingState:
    """Recording state file helpers."""

    def test_write_read_clear(self, tmp_path, monkeypatch):
        from meeting_minutes import recording_state

        state_dir = tmp_path / ".meeting-minutes"
        monkeypatch.setattr(recording_state, "_STATE_DIR", state_dir)
        monkeypatch.setattr(recording_state, "_STATE_FILE", state_dir / "recording_state.json")
        monkeypatch.setattr(recording_state, "_LEGACY_STATE_FILE", tmp_path / "legacy.json")

        assert recording_state.read_recording_state() is None

        recording_state.write_recording_state("meeting-123")
        state = recording_state.read_recording_state()
        assert state == {"meeting_id": "meeting-123"}

        recording_state.clear_recording_state()
        assert recording_state.read_recording_state() is None

    def test_legacy_fallback(self, tmp_path, monkeypatch):
        import json
        from meeting_minutes import recording_state

        state_dir = tmp_path / ".meeting-minutes"
        legacy = tmp_path / "legacy.json"
        monkeypatch.setattr(recording_state, "_STATE_DIR", state_dir)
        monkeypatch.setattr(recording_state, "_STATE_FILE", state_dir / "recording_state.json")
        monkeypatch.setattr(recording_state, "_LEGACY_STATE_FILE", legacy)

        legacy.write_text(json.dumps({"meeting_id": "old-meeting"}))
        state = recording_state.read_recording_state()
        assert state == {"meeting_id": "old-meeting"}

        recording_state.clear_recording_state()
        assert not legacy.exists()
