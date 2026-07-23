"""Regression tests for launchd plist generation (Zscaler TLS interception fix).

The NetApp corporate proxy (Zscaler) intercepts TLS, so the server process must
point OpenSSL/httpx/requests/huggingface_hub at the certifi+Zscaler CA bundle.
Delivering those env vars via the launchd plist (not just .env) is what makes the
fix survive every process restart. These tests lock that behavior in.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from meeting_minutes.system3.cli import _generate_plist

_CERT_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def test_plist_includes_cert_env_when_bundle_present(tmp_path):
    bundle = tmp_path / ".meeting-minutes" / "ca-bundle.pem"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("dummy")

    with patch("meeting_minutes.system3.cli.Path.home", return_value=tmp_path):
        xml = _generate_plist(Path("/proj"), "/proj/.venv/bin/mm", 8080)

    for var in _CERT_VARS:
        assert f"<key>{var}</key>" in xml
        assert str(bundle) in xml


def test_plist_omits_cert_env_when_bundle_absent(tmp_path):
    # No ca-bundle.pem under $HOME/.meeting-minutes → no cert vars emitted.
    with patch("meeting_minutes.system3.cli.Path.home", return_value=tmp_path):
        xml = _generate_plist(Path("/proj"), "/proj/.venv/bin/mm", 8080)

    for var in _CERT_VARS:
        assert var not in xml
