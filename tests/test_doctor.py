"""Tests for the ONB-1 doctor module."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meeting_minutes import doctor
from meeting_minutes.config import AppConfig


@pytest.fixture
def config(tmp_path):
    c = AppConfig(data_dir=str(tmp_path))
    c.storage.sqlite_path = str(tmp_path / "meetings.db")
    return c


# ---------------------------------------------------------------------------
# Python version
# ---------------------------------------------------------------------------


def test_python_ok():
    result = doctor.check_python_version()
    assert result.status == "ok"
    assert str(sys.version_info.major) in result.detail


def test_python_fail_when_old(monkeypatch):
    monkeypatch.setattr(doctor.sys, "version_info", (3, 10, 0, "final", 0))
    result = doctor.check_python_version()
    assert result.status == "fail"
    assert "3.11" in result.fix_hint


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------


def test_ffmpeg_ok(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/local/bin/ffmpeg")
    result = doctor.check_ffmpeg()
    assert result.status == "ok"


def test_ffmpeg_fail(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    result = doctor.check_ffmpeg()
    assert result.status == "fail"
    assert "brew install ffmpeg" in result.fix_command


# ---------------------------------------------------------------------------
# BlackHole device
# ---------------------------------------------------------------------------


def test_blackhole_skipped_off_mac(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    result = doctor.check_blackhole_device()
    assert result.status == "ok"
    assert "skip" in result.detail.lower()


def test_blackhole_found(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    fake_sd = SimpleNamespace(query_devices=lambda: [{"name": "Meeting Capture"}])
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    result = doctor.check_blackhole_device()
    assert result.status == "ok"


def test_blackhole_missing(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    fake_sd = SimpleNamespace(query_devices=lambda: [{"name": "Built-in Mic"}])
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    result = doctor.check_blackhole_device()
    assert result.status == "fail"
    assert "install.sh" in result.fix_command


def test_loopback_device_accepted(monkeypatch):
    """Rogue Amoeba Loopback device should count as a valid capture device."""
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    fake_sd = SimpleNamespace(query_devices=lambda: [{"name": "Loopback Audio"}])
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    result = doctor.check_blackhole_device()
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# HF token
# ---------------------------------------------------------------------------


def test_hf_token_missing(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    result = doctor.check_hf_token()
    assert result.status == "fail"


def test_hf_token_set_without_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    result = doctor.check_hf_token()
    assert result.status == "warn"


def test_hf_token_set_with_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    (tmp_path / "hub" / "models--pyannote--fake").mkdir(parents=True)
    result = doctor.check_hf_token()
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# LLM reachability
# ---------------------------------------------------------------------------


def test_llm_missing_key(config, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config.generation.llm.primary_provider = "anthropic"
    result = doctor.check_llm_reachable(config)
    assert result.status == "fail"
    assert "ANTHROPIC_API_KEY" in result.detail


def test_llm_ok_on_2xx(config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config.generation.llm.primary_provider = "anthropic"

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = None
    fake_client.post.return_value = SimpleNamespace(status_code=200)

    with patch("httpx.Client", return_value=fake_client):
        result = doctor.check_llm_reachable(config)
    assert result.status == "ok"


def test_llm_unauthorized_is_fail(config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    config.generation.llm.primary_provider = "anthropic"

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = None
    fake_client.post.return_value = SimpleNamespace(status_code=401)

    with patch("httpx.Client", return_value=fake_client):
        result = doctor.check_llm_reachable(config)
    assert result.status == "fail"
    assert "401" in result.detail or "reject" in result.detail.lower()


# ---------------------------------------------------------------------------
# DB integrity
# ---------------------------------------------------------------------------


def test_db_integrity_missing_db_warns(config):
    # Default config in fixture points to tmp_path/meetings.db — not created.
    result = doctor.check_database_integrity(config)
    assert result.status == "warn"
    assert "init" in result.fix_command


def test_db_integrity_ok_on_fresh_db(config):
    from meeting_minutes.system3.db import get_session_factory

    get_session_factory(f"sqlite:///{config.storage.sqlite_path}")
    result = doctor.check_database_integrity(config)
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# Disk space (re-uses DSK-1 math)
# ---------------------------------------------------------------------------


def test_disk_space_ok(config):
    result = doctor.check_disk_space(config)
    # On the test machine free >> estimated, so expect green/ok.
    assert result.status in ("ok", "warn")


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------


def test_gpu_reports_something(config):
    result = doctor.check_gpu()
    assert result.status in ("ok", "warn")
    assert result.detail


# ---------------------------------------------------------------------------
# Whisper model
# ---------------------------------------------------------------------------


def test_whisper_missing_cache_warns(config, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    result = doctor.check_whisper_model(config)
    assert result.status == "warn"
    assert "first recording" in result.fix_hint.lower()


def test_whisper_present_cache_ok(config, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    (tmp_path / "hub" / "models--openai--whisper-medium").mkdir(parents=True)
    result = doctor.check_whisper_model(config)
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# sqlite-vec
# ---------------------------------------------------------------------------


def test_sqlite_vec_check_runs(config):
    # sqlite-vec is in the venv, so this should pass.
    result = doctor.check_sqlite_vec()
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# WeasyPrint (EXP-1, optional PDF export)
# ---------------------------------------------------------------------------


def test_weasyprint_ok(monkeypatch):
    """Happy path: a dummy weasyprint module that imports and exposes HTML."""
    fake_weasy = SimpleNamespace(HTML=object)
    monkeypatch.setitem(sys.modules, "weasyprint", fake_weasy)
    result = doctor.check_weasyprint()
    assert result.status == "ok"
    assert "available" in result.detail.lower()


def test_weasyprint_missing_package(monkeypatch):
    """ImportError path: package not installed — warn (optional feature)."""
    # Remove any cached module so the import actually runs.
    monkeypatch.delitem(sys.modules, "weasyprint", raising=False)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "weasyprint":
            raise ImportError("No module named 'weasyprint'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = doctor.check_weasyprint()
    assert result.status == "warn"
    assert "pip install weasyprint" in result.fix_command


def test_weasyprint_missing_natives(monkeypatch):
    """OSError path: package present, libpango/cairo missing — warn."""
    monkeypatch.delitem(sys.modules, "weasyprint", raising=False)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "weasyprint":
            raise OSError("cannot load library 'libpango-1.0-0'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = doctor.check_weasyprint()
    assert result.status == "warn"
    assert "brew install" in result.fix_command
    assert "pango" in result.fix_command


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_run_checks_returns_eleven_in_order(config):
    results = doctor.run_checks(config)
    assert len(results) == 11
    names = [r.name for r in results]
    assert names[0] == "python_version"
    assert names[1] == "ffmpeg"
    assert names[-2] == "sqlite_vec"
    assert names[-1] == "weasyprint"


def test_overall_status_reflects_worst(monkeypatch, config):
    # Force an LLM failure and ensure the aggregate reports fail.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config.generation.llm.primary_provider = "anthropic"

    results = doctor.run_checks(config)
    statuses = {r.status for r in results}
    assert "fail" in statuses
