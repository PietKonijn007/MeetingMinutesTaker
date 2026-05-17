"""Tests for Phase 2 security hardening (SEC-2).

Covers:
- SQL injection fix in search (parameterized IN clause)
- Backup encryption (Fernet encrypt/decrypt round-trip)
- Global rate limiting middleware
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from meeting_minutes.api.rate_limit import RateLimitMiddleware


# ---------------------------------------------------------------------------
# Rate limiting middleware
# ---------------------------------------------------------------------------

def _make_rate_app(rpm: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, requests_per_minute=rpm)

    @app.get("/api/data")
    def data():
        return {"ok": True}

    @app.get("/other")
    def other():
        return {"ok": True}

    return app


class TestRateLimitMiddleware:
    def test_disabled_when_zero(self):
        client = TestClient(_make_rate_app(0))
        for _ in range(100):
            assert client.get("/api/data").status_code == 200

    def test_allows_under_limit(self):
        client = TestClient(_make_rate_app(10))
        for _ in range(10):
            assert client.get("/api/data").status_code == 200

    def test_blocks_over_limit(self):
        client = TestClient(_make_rate_app(5))
        for _ in range(5):
            client.get("/api/data")
        resp = client.get("/api/data")
        assert resp.status_code == 429
        assert "Rate limit" in resp.json()["detail"]
        assert resp.headers.get("Retry-After") == "60"

    def test_non_api_paths_exempt(self):
        client = TestClient(_make_rate_app(2))
        for _ in range(5):
            assert client.get("/other").status_code == 200


# ---------------------------------------------------------------------------
# Backup encryption round-trip
# ---------------------------------------------------------------------------

class TestBackupEncryption:
    def test_encrypt_decrypt_roundtrip(self, tmp_path):
        from cryptography.fernet import Fernet

        from meeting_minutes.backup import backup_database, restore_backup
        from meeting_minutes.encryption import is_encrypted

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'hello')")
        conn.commit()
        conn.close()

        key = Fernet.generate_key().decode()
        backup_dir = tmp_path / "backups"
        backup_file = backup_database(db_path, backup_dir, encryption_key=key)

        assert backup_file.exists()
        assert is_encrypted(backup_file)

        restored_db = tmp_path / "restored.db"
        restore_backup(backup_file, restored_db, encryption_key=key)
        conn = sqlite3.connect(str(restored_db))
        rows = conn.execute("SELECT val FROM t").fetchall()
        conn.close()
        assert rows == [("hello",)]

    def test_no_key_no_encryption(self, tmp_path):
        from meeting_minutes.backup import backup_database
        from meeting_minutes.encryption import is_encrypted

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()

        backup_file = backup_database(db_path, tmp_path / "backups")
        assert not is_encrypted(backup_file)


# ---------------------------------------------------------------------------
# SQL injection fix — verify parameterized query builds correctly
# ---------------------------------------------------------------------------

class TestSearchParameterized:
    def test_in_clause_uses_bind_params(self):
        """The search query must use :_fts_id_N bind params, not string interpolation."""
        import inspect
        from meeting_minutes.system3 import search

        source = inspect.getsource(search)
        assert "f\"'{mid}'\"" not in source, "String interpolation in IN clause still present"
        assert "_fts_id_" in source, "Parameterized _fts_id_ placeholders not found"
