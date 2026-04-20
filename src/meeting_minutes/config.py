"""Configuration loading and validation for Meeting Minutes Taker."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, model_validator


class RecordingConfig(BaseModel):
    audio_device: str = "auto"
    sample_rate: int = 16000
    format: str = "flac"
    auto_stop_silence_minutes: int = 5


class TranscriptionConfig(BaseModel):
    primary_engine: str = "whisper"  # whisper | whisper-cpp
    whisper_model: str = "medium"   # tiny, base, small, medium, large-v3, distil-*
    language: str = "auto"
    custom_vocabulary: str | None = None


class DiarizationConfig(BaseModel):
    enabled: bool = True
    engine: str = "pyannote"


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    timeout_seconds: int = 300  # Local models can be slow; generous timeout


class LLMConfig(BaseModel):
    primary_provider: str = "anthropic"  # anthropic | openai | openrouter | ollama
    model: str = "claude-sonnet-4-6"
    fallback_provider: str | None = "openai"
    fallback_model: str | None = "gpt-4o"
    temperature: float = 0.2
    max_output_tokens: int = 4096
    retry_attempts: int = 3
    timeout_seconds: int = 120
    ollama: OllamaConfig = OllamaConfig()


class GenerationConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    templates_dir: str = "templates"


class StorageConfig(BaseModel):
    database: str = "sqlite"
    sqlite_path: str = "db/meetings.db"


class PipelineConfig(BaseModel):
    mode: str = "automatic"  # automatic | semi_automatic | manual


class BackupConfig(BaseModel):
    enabled: bool = True
    backup_dir: str = "backups"
    interval_hours: int = 1  # min time between auto-backups


class ObsidianConfig(BaseModel):
    enabled: bool = False
    vault_path: str = ""  # e.g., "~/Documents/Obsidian Vault"


class SecurityConfig(BaseModel):
    encryption_enabled: bool = False
    encryption_key: str = ""  # Prefer setting MM_ENCRYPTION_KEY env var over storing key here

    @model_validator(mode="after")
    def _apply_env_key(self) -> "SecurityConfig":
        env_key = os.environ.get("MM_ENCRYPTION_KEY", "").strip()
        if env_key:
            self.encryption_key = env_key
        return self


class APIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    cors_origins: list[str] = [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


class RetentionConfig(BaseModel):
    audio_days: int = 90          # -1 = keep forever
    transcript_days: int = -1     # -1 = keep forever
    minutes_days: int = -1        # -1 = keep forever
    backup_days: int = 30         # -1 = keep forever


class DiskConfig(BaseModel):
    """Disk-space preflight + mid-recording watchdog (DSK-1)."""

    # Assumed recording length when the caller hasn't provided one.
    default_planned_minutes: int = 60
    # FLAC typically compresses speech to ~50% of raw PCM. We over-estimate
    # (0.6) so preflight reserves more headroom than it strictly needs.
    flac_compression_factor: float = 0.6
    # Watchdog poll interval while recording.
    watchdog_interval_seconds: int = 30
    # Trigger graceful stop when free < factor * remaining_estimate.
    watchdog_graceful_stop_factor: float = 0.5


class PerformanceConfig(BaseModel):
    """Hardware acceleration and performance tuning settings."""

    # Enable PYTORCH_ENABLE_MPS_FALLBACK: lets Metal GPU silently fall back to CPU
    # for ops not yet supported on MPS (instead of crashing). Recommended on
    # Apple Silicon — ~5-10x faster diarization than pure CPU. No effect on
    # non-Apple-Silicon hardware.
    pytorch_mps_fallback: bool = True


class NotificationsConfig(BaseModel):
    """Desktop notifications on pipeline events (NOT-1).

    ``enabled`` defaults to ``True`` on macOS (where ``pync`` ships desktop
    notifications via the Notification Center) and ``False`` elsewhere —
    pync is macOS-only. ``sound`` toggles the default notification sound.
    ``click_url_base`` is prepended to the meeting id when a user clicks
    the notification; defaults to the local ``mm serve`` host.
    """

    enabled: bool | None = None  # resolved in model_validator
    sound: bool = True
    click_url_base: str = "http://localhost:8080/meeting"

    @model_validator(mode="after")
    def _default_enabled(self) -> "NotificationsConfig":
        if self.enabled is None:
            import sys

            self.enabled = sys.platform == "darwin"
        return self


class BriefConfig(BaseModel):
    """Pre-meeting briefing page settings (BRF-1)."""

    # When True, the briefing endpoint runs a single two-sentence LLM
    # synthesis over the aggregated sections and attaches it as
    # ``summary``. Off by default — default briefings are purely DB-sourced.
    summarize_with_llm: bool = False


class ExportConfig(BaseModel):
    """Export settings (EXP-1)."""

    # Optional output directory override for the CLI; relative paths
    # resolve against ``data_dir`` when the CLI builds a default path.
    default_out_dir: str = "data/exports"


def resolve_db_path(sqlite_path: str) -> Path:
    """Resolve the sqlite_path config value to an absolute Path.

    Rules:
    - Absolute paths (starts with /) and ~ are used as-is.
    - Relative paths are resolved against the **project root** (the directory
      containing pyproject.toml), NOT the current working directory. This
      ensures the CLI (run from any directory) and launchd-spawned `mm serve`
      (run from the project root) both hit the same DB file.
    """
    p = Path(sqlite_path).expanduser()
    if not p.is_absolute():
        # project_root = parent of src/meeting_minutes/
        project_root = Path(__file__).resolve().parent.parent.parent
        p = project_root / p
    return p


class AppConfig(BaseModel):
    data_dir: str = "~/MeetingMinutesTaker/data"
    log_level: str = "INFO"
    pipeline: PipelineConfig = PipelineConfig()
    recording: RecordingConfig = RecordingConfig()
    transcription: TranscriptionConfig = TranscriptionConfig()
    diarization: DiarizationConfig = DiarizationConfig()
    generation: GenerationConfig = GenerationConfig()
    storage: StorageConfig = StorageConfig()
    backup: BackupConfig = BackupConfig()
    obsidian: ObsidianConfig = ObsidianConfig()
    api: APIConfig = APIConfig()
    retention: RetentionConfig = RetentionConfig()
    security: SecurityConfig = SecurityConfig()
    performance: PerformanceConfig = PerformanceConfig()
    disk: DiskConfig = DiskConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    brief: BriefConfig = BriefConfig()
    export: ExportConfig = ExportConfig()

    def model_post_init(self, __context) -> None:
        """Apply performance settings to process env vars (affects torch, etc.)."""
        import os
        import sys
        from pathlib import Path as _Path

        if self.performance.pytorch_mps_fallback:
            os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        else:
            os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)

        # EXP-1: WeasyPrint's ctypes.util.find_library() can't locate the
        # Homebrew-installed pango/cairo/gdk-pixbuf/libffi without
        # DYLD_FALLBACK_LIBRARY_PATH pointing at the brew prefix. Set it
        # here so it's in place before anyone imports weasyprint (which is
        # deferred to first PDF export). Runs for every entry point that
        # loads config — mm serve, mm export, mm doctor, mm repair, etc.
        # Apple Silicon puts brew at /opt/homebrew, Intel at /usr/local.
        if sys.platform == "darwin":
            for brew_lib in ("/opt/homebrew/lib", "/usr/local/lib"):
                if _Path(brew_lib).is_dir():
                    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
                    if brew_lib not in existing.split(":"):
                        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
                            f"{brew_lib}:{existing}" if existing else brew_lib
                        )
                    break


class ConfigLoader:
    """Load and validate YAML configuration."""

    @staticmethod
    def load(config_path: Path) -> AppConfig:
        """Load YAML config, apply defaults, validate required fields."""
        if not config_path.exists():
            # Return defaults if config file doesn't exist
            return AppConfig()

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        try:
            return AppConfig(**raw)
        except ValidationError as exc:
            raise ValueError(f"Invalid configuration in {config_path}: {exc}") from exc

    @staticmethod
    def load_default() -> AppConfig:
        """Load from default locations: ./config/config.yaml or ~/.meeting-minutes/config.yaml."""
        candidates = [
            Path("config/config.yaml"),
            Path.home() / ".meeting-minutes" / "config.yaml",
        ]
        for path in candidates:
            if path.exists():
                return ConfigLoader.load(path)
        return AppConfig()
