"""Enforce data retention policies — delete files older than configured thresholds."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def enforce_retention(config) -> dict:
    """Delete files exceeding retention thresholds. Returns counts of deleted items."""
    data_dir = Path(config.data_dir).expanduser()
    retention = config.retention
    deleted = {"audio": 0, "transcripts": 0, "minutes": 0, "backups": 0}
    now = datetime.now()

    if retention.audio_days > 0:
        deleted["audio"] = _cleanup_old_files(
            data_dir / "recordings", retention.audio_days, now, ["*.flac", "*.wav", "*.mp3", "*.m4a", "*.ogg"]
        )

    if retention.transcript_days > 0:
        deleted["transcripts"] = _cleanup_old_files(
            data_dir / "transcripts", retention.transcript_days, now, ["*.json"]
        )

    if retention.minutes_days > 0:
        deleted["minutes"] = _cleanup_old_files(
            data_dir / "minutes", retention.minutes_days, now, ["*.json", "*.md"]
        )

    if retention.backup_days > 0:
        backup_dir = Path(config.backup.backup_dir)
        deleted["backups"] = _cleanup_old_files(
            backup_dir, retention.backup_days, now, ["*.db"]
        )

    total = sum(deleted.values())
    if total > 0:
        logger.info("Retention cleanup: deleted %s", deleted)
    return deleted


def get_retention_status(config) -> dict:
    """Return file counts and oldest file ages per category."""
    data_dir = Path(config.data_dir).expanduser()
    retention = config.retention
    now = datetime.now()

    status = {
        "audio": _dir_stats(data_dir / "recordings", now, ["*.flac", "*.wav", "*.mp3", "*.m4a", "*.ogg"]),
        "transcripts": _dir_stats(data_dir / "transcripts", now, ["*.json"]),
        "minutes": _dir_stats(data_dir / "minutes", now, ["*.json", "*.md"]),
        "backups": _dir_stats(Path(config.backup.backup_dir), now, ["*.db"]),
    }

    # Include configured retention days
    status["config"] = {
        "audio_days": retention.audio_days,
        "transcript_days": retention.transcript_days,
        "minutes_days": retention.minutes_days,
        "backup_days": retention.backup_days,
    }

    return status


def _dir_stats(directory: Path, now: datetime, patterns: list[str]) -> dict:
    """Get file count and oldest file age for a directory."""
    if not directory.exists():
        return {"count": 0, "oldest_days": None}

    files = []
    for pattern in patterns:
        files.extend(directory.glob(pattern))

    if not files:
        return {"count": 0, "oldest_days": None}

    oldest_mtime = min(f.stat().st_mtime for f in files)
    oldest_age = (now - datetime.fromtimestamp(oldest_mtime)).days

    return {"count": len(files), "oldest_days": oldest_age}


def _cleanup_old_files(directory: Path, max_days: int, now: datetime, patterns: list[str]) -> int:
    """Delete files older than max_days in the given directory matching patterns."""
    if not directory.exists():
        return 0
    cutoff = now - timedelta(days=max_days)
    deleted = 0
    for pattern in patterns:
        for f in directory.glob(pattern):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
                    deleted += 1
                    logger.debug("Retention: deleted %s (age: %d days)", f.name, (now - mtime).days)
            except Exception as e:
                logger.warning("Retention: failed to delete %s: %s", f.name, e)
    return deleted
