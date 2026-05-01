"""First-run onboarding diagnostic (ONB-1).

Eleven independent checks the user can run via ``mm doctor`` or the
``/onboarding`` web page. Each check is a pure function returning a
``CheckResult`` with an ``ok | warn | fail`` status and a copy-pasteable
fix hint for the failure case.

None of the checks write to the database or the network; the LLM
reachability probe (#5) does one short remote call but bails quickly
on timeout.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from meeting_minutes.config import AppConfig, resolve_db_path

logger = logging.getLogger(__name__)


CheckStatus = str  # "ok" | "warn" | "fail"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str
    fix_hint: str = ""
    fix_command: str = ""  # optional shell command the UI can offer for copy-paste
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python_version() -> CheckResult:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        return CheckResult(
            name="python_version",
            status="ok",
            detail=f"Python {major}.{minor}.{sys.version_info.micro}",
        )
    return CheckResult(
        name="python_version",
        status="fail",
        detail=f"Python {major}.{minor} — need 3.11+",
        fix_hint="Install Python 3.11+ and recreate the venv",
    )


def check_ffmpeg() -> CheckResult:
    binary = shutil.which("ffmpeg")
    if binary:
        return CheckResult(
            name="ffmpeg",
            status="ok",
            detail=f"ffmpeg at {binary}",
        )
    return CheckResult(
        name="ffmpeg",
        status="fail",
        detail="ffmpeg not on PATH",
        fix_hint="Install ffmpeg (macOS) with Homebrew",
        fix_command="brew install ffmpeg",
    )


def check_blackhole_device() -> CheckResult:
    """Check 3 — virtual loopback capture device present (macOS only).

    Accepts BlackHole-based aggregate devices or Rogue Amoeba Loopback devices.
    """
    if platform.system() != "Darwin":
        return CheckResult(
            name="meeting_capture_device",
            status="ok",
            detail="Not macOS — skipped",
        )
    try:
        import sounddevice as sd  # type: ignore

        devices = sd.query_devices()
    except Exception as exc:
        return CheckResult(
            name="meeting_capture_device",
            status="warn",
            detail=f"Could not query audio devices: {exc}",
            fix_hint="Ensure PortAudio is installed; then re-run install.sh",
        )

    names = [d["name"].lower() for d in devices]
    for n in names:
        if (
            "meeting capture" in n
            or "meetingcapture" in n
            or "blackhole" in n
            or "loopback" in n
        ):
            return CheckResult(
                name="meeting_capture_device",
                status="ok",
                detail="Meeting Capture / BlackHole / Loopback device available",
            )
    return CheckResult(
        name="meeting_capture_device",
        status="fail",
        detail="No Meeting Capture, BlackHole, or Loopback device found",
        fix_hint="Re-run install.sh for BlackHole, or configure a Rogue Amoeba Loopback device (see docs/USER_GUIDE.md §3)",
        fix_command="./install.sh",
    )


def check_hf_token() -> CheckResult:
    """Check 4 — HF_TOKEN env var present and at least one pyannote model cached."""
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        return CheckResult(
            name="hf_token",
            status="fail",
            detail="HF_TOKEN env var not set",
            fix_hint="Set HF_TOKEN; accept the pyannote license at huggingface.co/pyannote/speaker-diarization-3.1",
            fix_command='export HF_TOKEN="<your_token>"',
        )
    # Check that at least one pyannote model is downloaded.
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    hub = cache / "hub"
    pyannote_dirs = []
    if hub.exists():
        for entry in hub.iterdir():
            if entry.is_dir() and "pyannote" in entry.name.lower():
                pyannote_dirs.append(entry.name)
    if not pyannote_dirs:
        return CheckResult(
            name="hf_token",
            status="warn",
            detail="HF_TOKEN set but no pyannote model cached yet",
            fix_hint="Run a recording once — pyannote models download on first use",
        )
    return CheckResult(
        name="hf_token",
        status="ok",
        detail=f"HF_TOKEN set; {len(pyannote_dirs)} pyannote model(s) cached",
    )


def check_llm_reachable(config: AppConfig) -> CheckResult:
    """Check 5 — a short remote dry-run against the configured LLM provider."""
    provider = config.generation.llm.primary_provider
    timeout = 5.0

    # Resolve the provider key so we don't crash on missing env vars.
    if provider == "ollama":
        url = config.generation.llm.ollama.base_url
        try:
            import httpx

            with httpx.Client(timeout=timeout) as client:
                resp = client.get(f"{url}/api/version")
                if resp.status_code == 200:
                    return CheckResult(
                        name="llm_reachable",
                        status="ok",
                        detail=f"Ollama responding at {url}",
                    )
                return CheckResult(
                    name="llm_reachable",
                    status="fail",
                    detail=f"Ollama at {url} returned HTTP {resp.status_code}",
                    fix_hint="Start Ollama and re-check",
                    fix_command="ollama serve",
                )
        except Exception as exc:
            return CheckResult(
                name="llm_reachable",
                status="fail",
                detail=f"Ollama at {url} unreachable: {exc}",
                fix_hint="Start Ollama",
                fix_command="ollama serve",
            )

    key_env = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }.get(provider)
    if key_env is None:
        return CheckResult(
            name="llm_reachable",
            status="warn",
            detail=f"Unknown provider: {provider}",
        )
    key = os.environ.get(key_env, "").strip()
    if not key:
        return CheckResult(
            name="llm_reachable",
            status="fail",
            detail=f"{key_env} not set",
            fix_hint=f"Set {key_env} in your .env or export it",
            fix_command=f'export {key_env}="<your_key>"',
        )

    # One-token dry-run completion. We only need to confirm the endpoint
    # accepts the key — we deliberately don't trust it further than that.
    try:
        import httpx

        if provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            body = {
                "model": config.generation.llm.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }
        elif provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
            body = {
                "model": config.generation.llm.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }
        else:  # openrouter
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
            body = {
                "model": config.generation.llm.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
        if resp.status_code < 400:
            return CheckResult(
                name="llm_reachable",
                status="ok",
                detail=f"{provider} reachable (HTTP {resp.status_code})",
            )
        if resp.status_code in (401, 403):
            return CheckResult(
                name="llm_reachable",
                status="fail",
                detail=f"{provider} rejected the key (HTTP {resp.status_code})",
                fix_hint=f"Check {key_env} in Settings",
            )
        return CheckResult(
            name="llm_reachable",
            status="warn",
            detail=f"{provider} returned HTTP {resp.status_code}",
        )
    except Exception as exc:
        return CheckResult(
            name="llm_reachable",
            status="fail",
            detail=f"{provider} unreachable: {exc}",
            fix_hint="Check your network / API key / provider URL in Settings",
        )


def check_database_integrity(config: AppConfig) -> CheckResult:
    """Check 6 — delegate to HLT-1's PRAGMA integrity_check."""
    db_path = resolve_db_path(config.storage.sqlite_path)
    if not db_path.exists():
        return CheckResult(
            name="database_integrity",
            status="warn",
            detail=f"Database not yet initialised at {db_path}",
            fix_hint="Run: mm init",
            fix_command="mm init",
        )

    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
    except Exception as exc:
        return CheckResult(
            name="database_integrity",
            status="fail",
            detail=f"Could not open DB: {exc}",
            fix_hint="Restore from backup or run: mm repair",
            fix_command="mm repair",
        )

    if row and row[0] == "ok":
        return CheckResult(
            name="database_integrity",
            status="ok",
            detail="PRAGMA integrity_check = ok",
        )
    return CheckResult(
        name="database_integrity",
        status="fail",
        detail=f"integrity_check reported: {row[0] if row else 'unknown'}",
        fix_hint="Run: mm repair",
        fix_command="mm repair",
    )


def check_disk_space(config: AppConfig) -> CheckResult:
    """Check 7 — DSK-1 preflight at default planned minutes."""
    from meeting_minutes.system1.capture import preflight_disk_check

    result = preflight_disk_check(config, planned_minutes=config.disk.default_planned_minutes)
    detail = (
        f"tier={result.tier} — free={result.free_bytes} "
        f"estimated={result.estimated_bytes} (planned {result.planned_minutes}min)"
    )
    if result.tier == "red":
        return CheckResult(
            name="disk_space",
            status="fail",
            detail=detail,
            fix_hint="Free up disk space or shorten planned_minutes",
        )
    if result.tier == "orange":
        return CheckResult(
            name="disk_space",
            status="warn",
            detail=detail,
            fix_hint="Consider freeing space before long recordings",
        )
    return CheckResult(name="disk_space", status="ok", detail=detail)


def check_gpu() -> CheckResult:
    """Check 8 — hardware.detect_hardware result."""
    try:
        from meeting_minutes.hardware import detect_hardware

        profile = detect_hardware()
    except Exception as exc:
        return CheckResult(
            name="gpu_detection",
            status="warn",
            detail=f"Hardware probe failed: {exc}",
            fix_hint="Review Hardware section in Settings",
        )

    if profile.gpu.type == "metal":
        return CheckResult(
            name="gpu_detection",
            status="ok",
            detail=f"Apple Silicon — {profile.gpu.name}, unified {profile.gpu.vram_gb:.0f}GB",
        )
    if profile.gpu.type == "cuda":
        return CheckResult(
            name="gpu_detection",
            status="ok",
            detail=f"CUDA — {profile.gpu.name}, {profile.gpu.vram_gb:.1f}GB VRAM",
        )
    return CheckResult(
        name="gpu_detection",
        status="warn",
        detail=f"No GPU detected — CPU fallback, {profile.total_ram_gb:.0f}GB RAM",
        fix_hint="Review Hardware section in Settings — transcription will be slower",
    )


def check_whisper_model(config: AppConfig) -> CheckResult:
    """Check 9 — at least one Whisper model file exists in the HF cache."""
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    hub = cache / "hub"
    model_hint = config.transcription.whisper_model
    found: list[str] = []
    if hub.exists():
        for entry in hub.iterdir():
            if entry.is_dir() and "whisper" in entry.name.lower():
                found.append(entry.name)
    if found:
        return CheckResult(
            name="whisper_model",
            status="ok",
            detail=f"{len(found)} whisper model(s) cached; configured: {model_hint}",
        )
    return CheckResult(
        name="whisper_model",
        status="warn",
        detail=f"No Whisper model cached at {hub} — will download on first recording",
        fix_hint="Model will download automatically on first recording",
    )


def check_sqlite_vec() -> CheckResult:
    """Check 10 — can we load the sqlite-vec extension in an isolated connection?"""
    try:
        import sqlite_vec  # type: ignore
    except ImportError as exc:
        return CheckResult(
            name="sqlite_vec",
            status="fail",
            detail=f"sqlite_vec package not importable: {exc}",
            fix_hint="Rebuild with: pip install -e .",
            fix_command="pip install -e .",
        )

    try:
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute("SELECT vec_version()").fetchone()
        conn.close()
    except Exception as exc:
        return CheckResult(
            name="sqlite_vec",
            status="fail",
            detail=f"Could not load sqlite-vec: {exc}",
            fix_hint="Rebuild with: pip install -e .",
            fix_command="pip install -e .",
        )
    return CheckResult(
        name="sqlite_vec",
        status="ok",
        detail="sqlite-vec loaded successfully",
    )


def check_weasyprint() -> CheckResult:
    """Check 11 — WeasyPrint importable and its native libs loadable (EXP-1).

    PDF export is optional, so both failure modes (missing Python package,
    missing native libs) surface as ``warn``, not ``fail``.
    """
    try:
        from weasyprint import HTML  # type: ignore  # noqa: F401
    except ImportError as exc:
        return CheckResult(
            name="weasyprint",
            status="warn",
            detail=f"weasyprint package not installed: {exc}",
            fix_hint="PDF export unavailable. Run: pip install weasyprint",
            fix_command="pip install weasyprint",
        )
    except OSError as exc:
        # WeasyPrint raises OSError when libpango / cairo / gdk-pixbuf /
        # libffi can't be located by ctypes.util.find_library().
        return CheckResult(
            name="weasyprint",
            status="warn",
            detail=f"WeasyPrint native libs not loadable: {exc}",
            fix_hint="PDF export unavailable — install native libs: brew install pango cairo gdk-pixbuf libffi",
            fix_command="brew install pango cairo gdk-pixbuf libffi",
        )
    return CheckResult(
        name="weasyprint",
        status="ok",
        detail="WeasyPrint available",
    )


def check_tesseract() -> CheckResult:
    """Check 12 — tesseract binary present for image OCR (spec/09).

    Image attachments need OCR to produce extracted text the summarizer
    can ground on. Both failure modes (Python wrapper missing, binary
    missing) surface as ``warn`` — image uploads still succeed, the
    user just gets an extraction error per image until they fix it.
    """
    try:
        import pytesseract  # type: ignore  # noqa: F401
    except ImportError as exc:
        return CheckResult(
            name="tesseract",
            status="warn",
            detail=f"pytesseract package not installed: {exc}",
            fix_hint="Image OCR unavailable. Run: pip install pytesseract",
            fix_command="pip install pytesseract",
        )
    binary = shutil.which("tesseract")
    if not binary:
        return CheckResult(
            name="tesseract",
            status="warn",
            detail="tesseract binary not on PATH",
            fix_hint=(
                "Image attachments will fail to extract text. Install "
                "tesseract (macOS): brew install tesseract"
            ),
            fix_command="brew install tesseract",
        )
    return CheckResult(
        name="tesseract",
        status="ok",
        detail=f"tesseract at {binary}",
    )


def check_poppler() -> CheckResult:
    """Check 13 — poppler binary present for scanned-PDF OCR fallback (spec/09).

    The PDF text-layer extractor handles 99% of PDFs without poppler.
    Poppler is only needed for scanned PDFs whose text layer is empty,
    where we render pages → images → tesseract. Surfaced as ``warn``
    because most attachment workflows never need it.

    Probe via the bundled ``pdftoppm`` binary, which is what
    ``pdf2image`` shells out to under the hood.
    """
    binary = shutil.which("pdftoppm")
    if not binary:
        return CheckResult(
            name="poppler",
            status="warn",
            detail="pdftoppm (poppler) not on PATH",
            fix_hint=(
                "Scanned PDFs will fall through to empty extraction. "
                "Install poppler (macOS): brew install poppler"
            ),
            fix_command="brew install poppler",
        )
    return CheckResult(
        name="poppler",
        status="ok",
        detail=f"poppler at {binary}",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_checks(config: AppConfig) -> list[CheckResult]:
    """Run all thirteen diagnostic checks in order."""
    return [
        check_python_version(),
        check_ffmpeg(),
        check_blackhole_device(),
        check_hf_token(),
        check_llm_reachable(config),
        check_database_integrity(config),
        check_disk_space(config),
        check_gpu(),
        check_whisper_model(config),
        check_sqlite_vec(),
        check_weasyprint(),
        check_tesseract(),
        check_poppler(),
    ]
