"""Automated SQLite database backups with rotation."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def backup_database(
    db_path: str | Path,
    backup_dir: str | Path = "backups",
    prefix: str = "meetings",
    encryption_key: str = "",
) -> Path:
    """Create a timestamped backup of the SQLite database.

    Uses SQLite's backup API for a consistent snapshot even while
    the database is being written to.  When *encryption_key* is provided,
    the backup file is Fernet-encrypted after creation.
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        backup_dir.chmod(0o700)
    except OSError:
        pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"{prefix}_{timestamp}.db"

    # Use SQLite backup API for consistency
    source = sqlite3.connect(str(db_path))
    dest = sqlite3.connect(str(backup_file))
    source.backup(dest)
    dest.close()
    source.close()

    if encryption_key:
        try:
            from meeting_minutes.encryption import encrypt_file

            encrypt_file(backup_file, encryption_key)
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to encrypt backup %s — saved unencrypted", backup_file.name,
            )

    try:
        backup_file.chmod(0o600)
    except OSError:
        pass

    return backup_file


def rotate_backups(
    backup_dir: str | Path,
    keep_hourly: int = 24,
    keep_daily: int = 7,
    keep_weekly: int = 4,
) -> int:
    """Rotate old backups, keeping specified counts.

    Returns number of backups deleted.
    """
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return 0

    backups = sorted(backup_dir.glob("meetings_*.db"), reverse=True)
    if not backups:
        return 0

    # Keep the most recent `keep_hourly` backups unconditionally
    keep = set(backups[:keep_hourly])

    # Keep one per day for the last `keep_daily` days
    days_seen: set[str] = set()
    for b in backups:
        day = b.stem.split("_")[1][:8]  # YYYYMMDD
        if day not in days_seen and len(days_seen) < keep_daily:
            keep.add(b)
            days_seen.add(day)

    # Keep one per week for the last `keep_weekly` weeks
    weeks_seen: set[str] = set()
    for b in backups:
        try:
            dt = datetime.strptime(b.stem.split("_", 1)[1][:8], "%Y%m%d")
            week = dt.strftime("%Y-W%W")
            if week not in weeks_seen and len(weeks_seen) < keep_weekly:
                keep.add(b)
                weeks_seen.add(week)
        except (ValueError, IndexError):
            continue

    # Delete everything not in the keep set
    deleted = 0
    for b in backups:
        if b not in keep:
            b.unlink()
            deleted += 1

    return deleted


def list_backups(backup_dir: str | Path = "backups") -> list[dict]:
    """List all available backups with metadata."""
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []

    result = []
    for b in sorted(backup_dir.glob("meetings_*.db"), reverse=True):
        size_mb = b.stat().st_size / (1024 * 1024)
        result.append(
            {
                "filename": b.name,
                "path": str(b),
                "size_mb": round(size_mb, 2),
                "created": datetime.fromtimestamp(b.stat().st_mtime).isoformat(),
            }
        )
    return result


def restore_backup(
    backup_file: str | Path,
    db_path: str | Path,
    encryption_key: str = "",
) -> None:
    """Restore a database from a backup file.

    If the backup is Fernet-encrypted (and *encryption_key* is provided),
    it is decrypted to a temporary file before the SQLite restore.
    """
    backup_file = Path(backup_file)
    db_path = Path(db_path)

    if not backup_file.exists():
        raise FileNotFoundError(f"Backup not found: {backup_file}")

    # Create a backup of the current DB before restoring
    if db_path.exists():
        pre_restore = db_path.with_suffix(".db.pre_restore")
        shutil.copy2(str(db_path), str(pre_restore))

    source_path = backup_file
    tmp_decrypted: Path | None = None
    if encryption_key:
        from meeting_minutes.encryption import is_encrypted

        if is_encrypted(backup_file):
            from meeting_minutes.encryption import decrypt_file

            tmp_decrypted = backup_file.with_suffix(".db.tmp_restore")
            tmp_decrypted.write_bytes(decrypt_file(backup_file, encryption_key))
            source_path = tmp_decrypted

    try:
        source = sqlite3.connect(str(source_path))
        dest = sqlite3.connect(str(db_path))
        source.backup(dest)
        dest.close()
        source.close()
    finally:
        if tmp_decrypted and tmp_decrypted.exists():
            tmp_decrypted.unlink()
