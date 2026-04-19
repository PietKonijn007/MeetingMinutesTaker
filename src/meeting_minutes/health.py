"""Startup health check + self-repair (HLT-1).

Runs a fixed set of integrity checks against the SQLite database and
on-disk artifacts, and exposes a ``repair`` entry point that rebuilds
derived indexes when the user opts in.

The module is deliberately tolerant of missing tables/files: some checks
target tables added by later batches and must skip cleanly when the
schema hasn't caught up yet.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from meeting_minutes.config import AppConfig, resolve_db_path
from meeting_minutes.pipeline_state import has_terminal_state

logger = logging.getLogger(__name__)


CheckStatus = str  # "ok" | "warn" | "fail"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str
    fix_hint: str = ""
    repairable: bool = False
    # Optional structured payload so the repair() phase has something to
    # work with without re-running the probe.
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class HealthReport:
    checks: list[CheckResult]
    overall_status: CheckStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class RepairLog:
    """One entry per check that repair() attempted."""

    actions: list[dict[str, Any]] = field(default_factory=list)

    def add(self, check: str, action: str, detail: str, dry_run: bool) -> None:
        self.actions.append({
            "check": check,
            "action": action,
            "detail": detail,
            "dry_run": dry_run,
        })

    def to_dict(self) -> dict[str, Any]:
        return {"actions": self.actions}


def _worst(statuses: list[CheckStatus]) -> CheckStatus:
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "ok"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_integrity(session: Session) -> CheckResult:
    """Check 1 — PRAGMA integrity_check.

    Hard-fail on corruption; we deliberately do NOT flag this repairable.
    SQLite corruption recovery is destructive and needs human judgement.
    """
    try:
        row = session.execute(sql_text("PRAGMA integrity_check")).fetchone()
        value = row[0] if row else ""
        if value == "ok":
            return CheckResult(
                name="sqlite_integrity",
                status="ok",
                detail="PRAGMA integrity_check = ok",
            )
        return CheckResult(
            name="sqlite_integrity",
            status="fail",
            detail=f"integrity_check reported: {value}",
            fix_hint="Restore from a recent backup: mm backup list && mm backup restore <file>",
            repairable=False,
        )
    except Exception as exc:
        return CheckResult(
            name="sqlite_integrity",
            status="fail",
            detail=f"Could not run integrity_check: {exc}",
            fix_hint="Inspect the SQLite file manually; restore from backup if unreadable.",
            repairable=False,
        )


def _check_fts_counts(session: Session) -> CheckResult:
    """Check 2 — meetings_fts row count matches meetings row count."""
    from meeting_minutes.system3.db import MeetingORM

    try:
        meetings_count = session.query(MeetingORM).count()
    except Exception as exc:
        return CheckResult(
            name="fts_row_count",
            status="fail",
            detail=f"Could not query meetings table: {exc}",
            fix_hint="Run: mm init",
            repairable=False,
        )

    try:
        fts_count = session.execute(
            sql_text("SELECT COUNT(*) FROM meetings_fts")
        ).scalar() or 0
    except Exception as exc:
        return CheckResult(
            name="fts_row_count",
            status="fail",
            detail=f"meetings_fts missing or unreadable: {exc}",
            fix_hint="Run: mm repair --check=fts_row_count",
            repairable=True,
            context={"meetings_count": meetings_count, "fts_count": 0},
        )

    if meetings_count == fts_count:
        return CheckResult(
            name="fts_row_count",
            status="ok",
            detail=f"meetings={meetings_count}, fts={fts_count}",
        )

    return CheckResult(
        name="fts_row_count",
        status="fail",
        detail=f"meetings={meetings_count} but fts={fts_count}",
        fix_hint="Run: mm repair --check=fts_row_count",
        repairable=True,
        context={"meetings_count": meetings_count, "fts_count": fts_count},
    )


def _check_embedding_vectors(session: Session) -> CheckResult:
    """Check 3 — every embedding_chunks.chunk_id has a row in embedding_vectors."""
    from meeting_minutes.system3.db import EmbeddingChunkORM

    try:
        chunk_count = session.query(EmbeddingChunkORM).count()
    except Exception as exc:
        return CheckResult(
            name="embedding_vectors",
            status="warn",
            detail=f"embedding_chunks unreadable: {exc}",
            fix_hint="Run: mm init",
        )

    if chunk_count == 0:
        return CheckResult(
            name="embedding_vectors",
            status="ok",
            detail="no embedding chunks yet",
        )

    # sqlite-vec virtual table may not be loaded — treat absence as warn,
    # not fail, since search still works via FTS fallback.
    try:
        orphan_ids = [
            row[0] for row in session.execute(sql_text(
                "SELECT ec.chunk_id FROM embedding_chunks ec "
                "LEFT JOIN embedding_vectors ev ON ec.chunk_id = ev.chunk_id "
                "WHERE ev.chunk_id IS NULL"
            )).fetchall()
        ]
    except Exception as exc:
        return CheckResult(
            name="embedding_vectors",
            status="warn",
            detail=f"embedding_vectors unreadable (sqlite-vec not loaded?): {exc}",
            fix_hint="Reinstall with: pip install -e .",
        )

    if not orphan_ids:
        return CheckResult(
            name="embedding_vectors",
            status="ok",
            detail=f"{chunk_count} chunks all have vectors",
        )

    # Which meetings own the orphan chunks? The repair phase re-embeds
    # per-meeting (reuses the same path ``mm embed`` uses).
    orphan_meeting_ids = {
        row[0] for row in session.query(EmbeddingChunkORM.meeting_id).filter(
            EmbeddingChunkORM.chunk_id.in_(orphan_ids)
        ).distinct().all()
    }
    return CheckResult(
        name="embedding_vectors",
        status="fail",
        detail=f"{len(orphan_ids)} chunk(s) missing vectors across {len(orphan_meeting_ids)} meeting(s)",
        fix_hint="Run: mm repair --check=embedding_vectors",
        repairable=True,
        context={
            "orphan_chunk_ids": orphan_ids,
            "meeting_ids": sorted(orphan_meeting_ids),
        },
    )


def _check_final_meetings_have_minutes(session: Session) -> CheckResult:
    """Check 4 — every meetings.meeting_id with status='final' has a minutes row."""
    from meeting_minutes.system3.db import MeetingORM, MinutesORM

    try:
        missing = (
            session.query(MeetingORM.meeting_id)
            .outerjoin(MinutesORM, MeetingORM.meeting_id == MinutesORM.meeting_id)
            .filter(MeetingORM.status == "final")
            .filter(MinutesORM.meeting_id.is_(None))
            .all()
        )
    except Exception as exc:
        return CheckResult(
            name="final_meetings_have_minutes",
            status="warn",
            detail=f"Could not evaluate: {exc}",
        )

    if not missing:
        return CheckResult(
            name="final_meetings_have_minutes",
            status="ok",
            detail="all final meetings have minutes",
        )

    ids = [m[0] for m in missing]
    # Warn (not fail) — we can't safely regenerate minutes for the user.
    return CheckResult(
        name="final_meetings_have_minutes",
        status="warn",
        detail=f"{len(ids)} final meeting(s) missing minutes",
        fix_hint="Inspect manually and re-run: mm generate <meeting_id>",
        context={"meeting_ids": ids},
    )


def _check_audio_files(session: Session, config: AppConfig) -> CheckResult:
    """Check 5 — audio_file_path exists OR pipeline has terminal state."""
    from meeting_minutes.system3.db import TranscriptORM

    try:
        rows = session.query(
            TranscriptORM.meeting_id, TranscriptORM.audio_file_path
        ).filter(TranscriptORM.audio_file_path.isnot(None)).all()
    except Exception as exc:
        return CheckResult(
            name="audio_files_present",
            status="warn",
            detail=f"Could not evaluate: {exc}",
        )

    missing_but_preserved: list[str] = []
    missing_and_unsafe: list[str] = []
    for meeting_id, audio_path in rows:
        if not audio_path:
            continue
        path = Path(audio_path).expanduser()
        if path.exists():
            continue
        if has_terminal_state(session, meeting_id):
            missing_but_preserved.append(meeting_id)
        else:
            missing_and_unsafe.append(meeting_id)

    if not missing_but_preserved and not missing_and_unsafe:
        return CheckResult(
            name="audio_files_present",
            status="ok",
            detail=f"all {len(rows)} referenced audio files present",
        )

    if missing_and_unsafe:
        return CheckResult(
            name="audio_files_present",
            status="warn",
            detail=(
                f"{len(missing_and_unsafe)} meeting(s) reference missing audio "
                f"and have not reached a terminal pipeline state"
            ),
            fix_hint="Inspect: mm status <meeting_id>",
            context={"missing_unsafe": missing_and_unsafe,
                     "missing_but_preserved": missing_but_preserved},
        )

    # All misses are retention-driven (terminal state). Surface as warn so
    # the user sees the count, but it's expected behaviour.
    return CheckResult(
        name="audio_files_present",
        status="warn",
        detail=(
            f"{len(missing_but_preserved)} audio file(s) removed by retention "
            f"(pipeline terminal — expected)"
        ),
        fix_hint="",
        context={"missing_but_preserved": missing_but_preserved},
    )


def _check_voice_samples(session: Session) -> CheckResult:
    """Check 6 — person_voice_samples.meeting_id existence (SPK-1 future).

    Table doesn't exist yet — skip cleanly.
    """
    try:
        session.execute(sql_text("SELECT 1 FROM person_voice_samples LIMIT 1"))
    except Exception:
        return CheckResult(
            name="voice_samples_orphan",
            status="ok",
            detail="person_voice_samples table not present — skipped",
        )

    try:
        orphans = session.execute(sql_text(
            "SELECT COUNT(*) FROM person_voice_samples vs "
            "LEFT JOIN meetings m ON vs.meeting_id = m.meeting_id "
            "WHERE m.meeting_id IS NULL"
        )).scalar() or 0
    except Exception as exc:
        return CheckResult(
            name="voice_samples_orphan",
            status="warn",
            detail=f"Could not evaluate: {exc}",
        )

    if orphans == 0:
        return CheckResult(
            name="voice_samples_orphan",
            status="ok",
            detail="no orphaned voice samples",
        )
    return CheckResult(
        name="voice_samples_orphan",
        status="warn",
        detail=f"{orphans} voice sample(s) reference a deleted meeting",
        fix_hint="Will be cleaned up automatically next repair cycle",
        repairable=True,
        context={"orphan_count": orphans},
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def check_all(session: Session, config: AppConfig) -> HealthReport:
    """Run all health checks against ``session`` and return a HealthReport."""
    results: list[CheckResult] = [
        _check_integrity(session),
        _check_fts_counts(session),
        _check_embedding_vectors(session),
        _check_final_meetings_have_minutes(session),
        _check_audio_files(session, config),
        _check_voice_samples(session),
    ]
    overall = _worst([r.status for r in results])
    return HealthReport(checks=results, overall_status=overall)


# ---------------------------------------------------------------------------
# Repair recipes
# ---------------------------------------------------------------------------


def _repair_fts(session: Session, *, dry_run: bool, log: RepairLog) -> None:
    """Rebuild meetings_fts from the underlying meetings/minutes/transcripts tables."""
    from meeting_minutes.system3.db import (
        FTS5_CREATE_SQL,
        MeetingORM,
        MinutesORM,
        TranscriptORM,
    )

    if dry_run:
        log.add("fts_row_count", "plan", "Would drop + recreate meetings_fts and reinsert all rows", True)
        return

    session.execute(sql_text("DROP TABLE IF EXISTS meetings_fts"))
    session.execute(sql_text(FTS5_CREATE_SQL))

    rows = (
        session.query(
            MeetingORM.meeting_id,
            MeetingORM.title,
            TranscriptORM.full_text,
            MinutesORM.markdown_content,
        )
        .outerjoin(TranscriptORM, MeetingORM.meeting_id == TranscriptORM.meeting_id)
        .outerjoin(MinutesORM, MeetingORM.meeting_id == MinutesORM.meeting_id)
        .all()
    )

    for mid, title, tt, mt in rows:
        session.execute(
            sql_text(
                "INSERT INTO meetings_fts(meeting_id, title, transcript_text, minutes_text) "
                "VALUES (:mid, :title, :tt, :mt)"
            ),
            {"mid": mid, "title": title or "", "tt": tt or "", "mt": mt or ""},
        )
    session.commit()
    log.add("fts_row_count", "rebuild", f"Reindexed {len(rows)} meeting(s) into meetings_fts", False)


def _repair_embedding_vectors(
    session: Session,
    config: AppConfig,
    meeting_ids: list[str],
    *,
    dry_run: bool,
    log: RepairLog,
) -> None:
    """Re-embed the given meetings — same path ``mm embed`` uses."""
    if not meeting_ids:
        log.add("embedding_vectors", "noop", "No orphan meetings found", dry_run)
        return

    if dry_run:
        log.add(
            "embedding_vectors",
            "plan",
            f"Would re-embed {len(meeting_ids)} meeting(s): {', '.join(mid[:8] for mid in meeting_ids)}",
            True,
        )
        return

    from meeting_minutes.embeddings import EmbeddingEngine

    engine = EmbeddingEngine(config)
    data_dir = Path(config.data_dir).expanduser()

    embedded = 0
    for mid in meeting_ids:
        try:
            count = engine.index_meeting(mid, session, data_dir)
            embedded += count
            log.add(
                "embedding_vectors", "reindex",
                f"Re-embedded {mid[:12]} → {count} chunks", False,
            )
        except Exception as exc:
            log.add(
                "embedding_vectors", "error",
                f"Failed to re-embed {mid[:12]}: {exc}", False,
            )
    log.add(
        "embedding_vectors", "summary",
        f"{embedded} chunks re-indexed across {len(meeting_ids)} meeting(s)", False,
    )


def _repair_voice_samples(session: Session, *, dry_run: bool, log: RepairLog) -> None:
    """Delete person_voice_samples rows whose meeting_id no longer exists."""
    try:
        session.execute(sql_text("SELECT 1 FROM person_voice_samples LIMIT 1"))
    except Exception:
        log.add("voice_samples_orphan", "noop", "Table not present — skipped", dry_run)
        return

    if dry_run:
        count = session.execute(sql_text(
            "SELECT COUNT(*) FROM person_voice_samples vs "
            "LEFT JOIN meetings m ON vs.meeting_id = m.meeting_id "
            "WHERE m.meeting_id IS NULL"
        )).scalar() or 0
        log.add("voice_samples_orphan", "plan", f"Would delete {count} orphan sample(s)", True)
        return

    session.execute(sql_text(
        "DELETE FROM person_voice_samples WHERE meeting_id IN ("
        "SELECT vs.meeting_id FROM person_voice_samples vs "
        "LEFT JOIN meetings m ON vs.meeting_id = m.meeting_id "
        "WHERE m.meeting_id IS NULL)"
    ))
    session.commit()
    log.add("voice_samples_orphan", "delete", "Removed orphan voice samples", False)


# Map of check name → (repair function signature consumes the CheckResult)
def _repair_dispatch(
    result: CheckResult,
    session: Session,
    config: AppConfig,
    *,
    dry_run: bool,
    log: RepairLog,
) -> None:
    name = result.name
    if name == "fts_row_count":
        _repair_fts(session, dry_run=dry_run, log=log)
    elif name == "embedding_vectors":
        meeting_ids = result.context.get("meeting_ids", [])
        _repair_embedding_vectors(
            session, config, meeting_ids, dry_run=dry_run, log=log,
        )
    elif name == "voice_samples_orphan":
        _repair_voice_samples(session, dry_run=dry_run, log=log)
    else:
        log.add(name, "noop", "No repair recipe for this check", dry_run)


def repair(
    report: HealthReport,
    session: Session,
    config: AppConfig,
    *,
    dry_run: bool = False,
    only: str | None = None,
) -> RepairLog:
    """Run the repair recipe for each repairable check.

    ``only`` restricts repair to a single check name. ``dry_run`` prints
    the plan without mutating the database.
    """
    log = RepairLog()
    for result in report.checks:
        if only is not None and result.name != only:
            continue
        if not result.repairable or result.status == "ok":
            if only is not None and result.name == only:
                log.add(result.name, "noop", f"Status={result.status}, nothing to repair", dry_run)
            continue
        _repair_dispatch(result, session, config, dry_run=dry_run, log=log)
    return log
