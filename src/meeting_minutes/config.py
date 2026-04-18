"""Configuration loading and validation for Meeting Minutes Taker."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError


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
    encryption_key: str = ""  # Fernet key — generate with: mm generate-key


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


class PerformanceConfig(BaseModel):
    """Hardware acceleration and performance tuning settings."""

    # Enable PYTORCH_ENABLE_MPS_FALLBACK: lets Metal GPU silently fall back to CPU
    # for ops not yet supported on MPS (instead of crashing). Recommended on
    # Apple Silicon — ~5-10x faster diarization than pure CPU. No effect on
    # non-Apple-Silicon hardware.
    pytorch_mps_fallback: bool = True


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

    def model_post_init(self, __context) -> None:
        """Apply performance settings to process env vars (affects torch, etc.)."""
        import os
        if self.performance.pytorch_mps_fallback:
            os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        else:
            os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)


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
