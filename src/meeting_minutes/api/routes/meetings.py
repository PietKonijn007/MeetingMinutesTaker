"""Meeting endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session, get_search, get_storage
from meeting_minutes.api.rate_limit import check_llm_limit
from meeting_minutes.api.schemas import (
    ActionItemResponse,
    DecisionResponse,
    ErrorResponse,
    ExportRequest,
    MeetingDetail,
    MeetingListItem,
    MeetingUpdate,
    MinutesResponse,
    PaginatedResponse,
    PersonResponse,
    TalkTimeAnalyticsResponse,
    TranscriptResponse,
)
from meeting_minutes.config import AppConfig
from meeting_minutes.models import SearchQuery
from meeting_minutes.system3.db import MeetingORM
from meeting_minutes.system3.search import SearchEngine
from meeting_minutes.system3.storage import MeetingFilters, StorageEngine

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meeting_to_list_item(m: MeetingORM) -> MeetingListItem:
    # A4: Compute effectiveness score from structured JSON for list view
    effectiveness_score = 0
    if m.minutes and m.minutes.structured_json:
        try:
            import json
            structured = json.loads(m.minutes.structured_json)
            eff = structured.get("meeting_effectiveness") or {}
            score = 0
            if eff.get("had_clear_agenda"):
                score += 1
            if eff.get("decisions_made", 0) > 0:
                score += 1
            if eff.get("action_items_assigned", 0) > 0:
                score += 1
            if eff.get("unresolved_items", 0) == 0:
                score += 1
            if len(structured.get("discussion_points", [])) >= 2 and len(structured.get("action_items", [])) >= 1:
                score += 1
            effectiveness_score = min(score, 5)
        except Exception:
            pass

    return MeetingListItem(
        meeting_id=m.meeting_id,
        title=m.title,
        date=m.date.isoformat() if m.date else None,
        meeting_type=m.meeting_type,
        duration=m.duration,
        organizer=m.organizer,
        summary=m.minutes.summary if m.minutes else None,
        attendee_names=[a.name for a in m.attendees],
        action_item_count=len(m.action_items),
        decision_count=len(m.decisions),
        effectiveness_score=effectiveness_score,
    )


def _meeting_to_detail(m: MeetingORM) -> MeetingDetail:
    attendees = [
        PersonResponse(person_id=a.person_id, name=a.name, email=a.email)
        for a in m.attendees
    ]
    minutes = None
    if m.minutes:
        # Parse structured JSON to expose discussion points, risks, follow-ups, etc.
        sentiment = None
        discussion_points: list[dict] = []
        risks_and_concerns: list[dict] = []
        follow_ups: list[dict] = []
        parking_lot: list[str] = []
        key_topics: list[str] = []
        sections: list[dict] = []

        import json as _json
        structured = None
        file_data = None

        # Primary source: DB column (new meetings)
        if m.minutes.structured_json:
            try:
                structured = _json.loads(m.minutes.structured_json)
            except Exception:
                structured = None

        # Load the on-disk minutes JSON — always, because sections[] lives
        # only on disk (never in DB), and we also use it as a fallback for
        # older meetings whose DB structured_json is NULL.
        try:
            from pathlib import Path as _Path
            from meeting_minutes.config import ConfigLoader
            _cfg = ConfigLoader.load_default()
            minutes_file = _Path(_cfg.data_dir).expanduser() / "minutes" / f"{m.meeting_id}.json"
            if not minutes_file.exists():
                # Fallback to repo-relative path
                minutes_file = _Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "minutes" / f"{m.meeting_id}.json"
            if minutes_file.exists():
                with open(minutes_file, "r", encoding="utf-8") as f:
                    file_data = _json.load(f)
        except Exception:
            file_data = None

        # If DB didn't have structured data, fall back to file's structured_data
        # or synthesize one from top-level fields (older schema)
        if not structured and file_data:
            structured = file_data.get("structured_data") or {
                "sentiment": file_data.get("sentiment"),
                "discussion_points": file_data.get("discussion_points", []),
                "risks_and_concerns": file_data.get("risks_and_concerns", []),
                "follow_ups": file_data.get("follow_ups", []),
                "parking_lot": file_data.get("parking_lot", []),
                "key_topics": file_data.get("key_topics", []),
            }

        # Sections from the text+regex fallback path — always come from disk
        if file_data:
            for s in file_data.get("sections", []) or []:
                if isinstance(s, dict):
                    sections.append({
                        "heading": s.get("heading") or "",
                        "content": s.get("content") or "",
                        "type": s.get("type"),
                    })

        if structured:
            sentiment = structured.get("sentiment")
            discussion_points = structured.get("discussion_points", []) or []
            risks_and_concerns = structured.get("risks_and_concerns", []) or []
            follow_ups = structured.get("follow_ups", []) or []
            parking_lot = structured.get("parking_lot", []) or []
            key_topics = structured.get("key_topics", []) or []

        minutes = MinutesResponse(
            minutes_id=m.minutes.minutes_id,
            summary=m.minutes.summary,
            markdown_content=m.minutes.markdown_content,
            generated_at=m.minutes.generated_at.isoformat() if m.minutes.generated_at else None,
            llm_model=m.minutes.llm_model,
            review_status=m.minutes.review_status,
            sentiment=sentiment,
            discussion_points=discussion_points,
            risks_and_concerns=risks_and_concerns,
            follow_ups=follow_ups,
            parking_lot=parking_lot,
            key_topics=key_topics,
            sections=sections,
        )
    action_items = [
        ActionItemResponse(
            action_item_id=ai.action_item_id,
            description=ai.description,
            owner=ai.owner,
            due_date=ai.due_date,
            status=ai.status or "open",
            meeting_id=m.meeting_id,
            meeting_title=m.title,
        )
        for ai in m.action_items
    ]
    decisions = [
        DecisionResponse(
            decision_id=d.decision_id,
            description=d.description,
            made_by=d.made_by,
            mentioned_at_seconds=d.mentioned_at_seconds,
            meeting_id=m.meeting_id,
            meeting_title=m.title,
            meeting_date=m.date.isoformat() if m.date else None,
        )
        for d in m.decisions
    ]
    transcript_text = m.transcript.full_text if m.transcript else None
    audio_file_path = m.transcript.audio_file_path if m.transcript else None

    # Extract participant sentiments and effectiveness score from structured JSON
    participant_sentiments: dict[str, str] = {}
    effectiveness_score = 0

    if m.minutes and m.minutes.structured_json:
        try:
            import json
            structured = json.loads(m.minutes.structured_json)

            # N7: Per-speaker sentiment
            for p in structured.get("participants", []):
                if p.get("name") and p.get("sentiment"):
                    participant_sentiments[p["name"]] = p["sentiment"]

            # A4: Effectiveness score (1-5, computed from structured data)
            eff = structured.get("meeting_effectiveness") or {}
            score = 0
            if eff.get("had_clear_agenda"):
                score += 1
            if eff.get("decisions_made", 0) > 0:
                score += 1
            if eff.get("action_items_assigned", 0) > 0:
                score += 1
            if eff.get("unresolved_items", 0) == 0:
                score += 1
            # Bonus point: has substantial discussion + outcomes
            if len(structured.get("discussion_points", [])) >= 2 and len(structured.get("action_items", [])) >= 1:
                score += 1
            effectiveness_score = min(score, 5)
        except Exception:
            pass

    return MeetingDetail(
        meeting_id=m.meeting_id,
        title=m.title,
        date=m.date.isoformat() if m.date else None,
        meeting_type=m.meeting_type,
        duration=m.duration,
        organizer=m.organizer,
        status=m.status,
        attendees=attendees,
        minutes=minutes,
        action_items=action_items,
        decisions=decisions,
        transcript_text=transcript_text,
        audio_file_path=audio_file_path,
        participant_sentiments=participant_sentiments,
        effectiveness_score=effectiveness_score,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse)
def list_meetings(
    storage: Annotated[StorageEngine, Depends(get_storage)],
    search_engine: Annotated[SearchEngine, Depends(get_search)],
    session: Annotated[Session, Depends(get_db_session)],
    q: Optional[str] = Query(None, description="Full-text search query"),
    type: Optional[str] = Query(None, alias="type", description="Meeting type filter"),
    after: Optional[str] = Query(None, description="After date (ISO)"),
    before: Optional[str] = Query(None, description="Before date (ISO)"),
    person: Optional[str] = Query(None, description="Attendee email filter"),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List meetings with optional filtering and search."""
    if q:
        # Use SearchEngine for full-text queries
        parsed = search_engine.parse_query(q)
        if type:
            parsed.meeting_type = type
        if after:
            try:
                parsed.after_date = datetime.fromisoformat(after)
            except ValueError:
                pass
        if before:
            try:
                parsed.before_date = datetime.fromisoformat(before)
            except ValueError:
                pass
        parsed.limit = limit
        parsed.offset = offset

        results = search_engine.search(parsed)

        # Hydrate ORM objects for rich response
        items: list[MeetingListItem] = []
        for r in results.results:
            m = storage.get_meeting(r.meeting_id)
            if m:
                item = _meeting_to_list_item(m)
                # Override summary with search snippet when available
                if r.snippet:
                    item.summary = r.snippet
                items.append(item)

        return PaginatedResponse(
            items=[i.model_dump() for i in items],
            total=results.total_count,
            limit=limit,
            offset=offset,
        )

    # No search query — use StorageEngine
    after_dt = None
    before_dt = None
    if after:
        try:
            after_dt = datetime.fromisoformat(after)
        except ValueError:
            pass
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            pass

    filters = MeetingFilters(
        meeting_type=type,
        after_date=after_dt,
        before_date=before_dt,
        attendee=person,
    )
    meetings = storage.list_meetings(limit=limit, offset=offset, filters=filters)

    # Total count (without limit/offset)
    total_query = session.query(MeetingORM)
    if type:
        total_query = total_query.filter(MeetingORM.meeting_type == type)
    if after_dt:
        total_query = total_query.filter(MeetingORM.date >= after_dt)
    if before_dt:
        total_query = total_query.filter(MeetingORM.date <= before_dt)
    total = total_query.count()

    items = [_meeting_to_list_item(m) for m in meetings]
    return PaginatedResponse(
        items=[i.model_dump() for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{meeting_id}", response_model=MeetingDetail)
def get_meeting(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
):
    """Get full meeting detail."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    return _meeting_to_detail(m)


@router.get("/{meeting_id}/transcript", response_model=TranscriptResponse)
def get_transcript(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Get full transcript for a meeting (includes segments + speaker labels)."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    if m.transcript is None:
        raise HTTPException(status_code=404, detail="No transcript available for this meeting")

    # Load segments + speakers from the transcript JSON file
    segments: list = []
    speakers: list = []
    data_dir = Path(config.data_dir).expanduser()
    transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"
    if transcript_path.exists():
        try:
            import json as _json
            data = _json.loads(transcript_path.read_text())
            raw_segments = data.get("transcript", {}).get("segments", []) or []
            for s in raw_segments:
                segments.append({
                    "id": s.get("id", 0),
                    "start": s.get("start"),
                    "end": s.get("end"),
                    "start_time": s.get("start"),
                    "end_time": s.get("end"),
                    "speaker": s.get("speaker"),
                    "text": s.get("text", ""),
                })
            for sp in data.get("speakers", []) or []:
                if isinstance(sp, dict):
                    speakers.append(sp)
        except Exception:
            pass

    return TranscriptResponse(
        meeting_id=meeting_id,
        full_text=m.transcript.full_text,
        language=m.transcript.language,
        audio_file_path=m.transcript.audio_file_path,
        segments=segments,
        speakers=speakers,
    )


@router.get("/{meeting_id}/speaker-suggestions")
def get_speaker_suggestions(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Return per-cluster speaker suggestions produced at diarization time (SPK-1).

    Each entry is ``{cluster_id, suggested_person_id, suggested_name,
    suggestion_score, suggestion_tier, speech_seconds}``. Clusters without a
    suggestion are still returned so the UI can decide whether to offer a
    "create new person" flow for long-enough unknown speakers.
    """
    import json as _json

    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    data_dir = Path(config.data_dir).expanduser()
    transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="No transcript JSON on disk")

    try:
        data = _json.loads(transcript_path.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read transcript: {exc}")

    # Compute cumulative speech seconds per cluster for the "long unknown"
    # inline-create hint (> 30 s threshold).
    from meeting_minutes.system1.speaker_identity import cluster_speech_durations

    segments = data.get("transcript", {}).get("segments", []) or []
    durations = cluster_speech_durations(segments)

    suggestions = []
    for sp in data.get("speakers", []) or []:
        if not isinstance(sp, dict):
            continue
        label = sp.get("label")
        suggestions.append({
            "cluster_id": label,
            "current_name": sp.get("name"),
            "suggested_person_id": sp.get("suggested_person_id"),
            "suggested_name": sp.get("suggested_name"),
            "suggestion_score": float(sp.get("suggestion_score", 0.0) or 0.0),
            "suggestion_tier": sp.get("suggestion_tier"),
            "speech_seconds": round(durations.get(label, 0.0), 2),
        })
    return {"meeting_id": meeting_id, "suggestions": suggestions}


@router.patch("/{meeting_id}/transcript/speakers")
def update_transcript_speakers(
    meeting_id: str,
    body: dict,
    config: Annotated[AppConfig, Depends(get_config)],
    storage: Annotated[StorageEngine, Depends(get_storage)],
    session: Annotated[Session, Depends(get_db_session)],
):
    """Rename speakers in a transcript. Applies to both segment labels and speakers array.

    Request body options (choose one):
        { "mapping": {"SPEAKER_00": "Tom", "SPEAKER_01": "Mary"} }
        { "ordered_names": ["Tom", "Mary"] }  # first-appearance order

    Optional SPK-1 field to confirm the voice sample under a specific person:
        { "mapping": {...}, "person_mapping": {"SPEAKER_00": "p-jon"} }

    When ``person_mapping`` is provided, confirm_sample() fires for each
    cluster-to-person pair, and any prior sample for that cluster under a
    different person is demoted to confirmed=False (invalidate_contamination).

    Also updates data/notes/{meeting_id}.json so the names persist for
    regenerate/reprocess.
    """
    import json as _json

    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    data_dir = Path(config.data_dir).expanduser()
    transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="No transcript JSON on disk")

    data = _json.loads(transcript_path.read_text())
    segments = data.get("transcript", {}).get("segments", []) or []

    # Build mapping from request body
    mapping: dict[str, str] = {}
    if body.get("mapping"):
        mapping = {k: v.strip() for k, v in body["mapping"].items() if v and v.strip()}
    elif body.get("ordered_names"):
        ordered = body["ordered_names"]
        if isinstance(ordered, str):
            ordered = [n.strip() for n in ordered.split(",") if n.strip()]
        # First-appearance order from segments
        seen: list[str] = []
        for seg in sorted(segments, key=lambda s: s.get("start") or 0):
            spk = seg.get("speaker")
            if spk and spk not in seen:
                seen.append(spk)
        for i, label in enumerate(seen):
            if i < len(ordered) and ordered[i]:
                mapping[label] = ordered[i].strip()
    else:
        raise HTTPException(status_code=400, detail="Provide either 'mapping' or 'ordered_names'")

    if not mapping:
        return {"updated": 0, "mapping": {}}

    # Rewrite segment speakers
    updated = 0
    for seg in segments:
        if seg.get("speaker") in mapping:
            seg["speaker"] = mapping[seg["speaker"]]
            updated += 1

    # Rebuild speakers array preserving order from segments
    seen_labels: list[str] = []
    for seg in segments:
        spk = seg.get("speaker")
        if spk and spk not in seen_labels:
            seen_labels.append(spk)
    data["speakers"] = [{"label": l, "name": None, "email": None, "confidence": 0.0} for l in seen_labels]
    data["transcript"]["segments"] = segments

    transcript_path.write_text(_json.dumps(data, indent=2, default=str))

    # Update the notes file so reprocess/regenerate use these names
    notes_dir = data_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes_file = notes_dir / f"{meeting_id}.json"
    notes_data = {}
    if notes_file.exists():
        try:
            notes_data = _json.loads(notes_file.read_text())
        except Exception:
            notes_data = {}
    notes_data["speakers"] = seen_labels
    notes_file.write_text(_json.dumps(notes_data, indent=2))

    # SPK-1: when the caller supplies person_mapping, confirm or invalidate
    # the voice samples for each relabelled cluster.
    spk1_result: dict = {"confirmed": 0, "invalidated": 0}
    person_mapping = body.get("person_mapping") or {}
    if isinstance(person_mapping, dict) and person_mapping:
        from meeting_minutes.system1 import speaker_identity as si

        for cluster_id, person_id in person_mapping.items():
            if not cluster_id:
                continue
            # Demote any sample previously written for this cluster under a
            # different person.
            invalidated = si.invalidate_contamination(
                session, meeting_id, cluster_id, person_id,
            )
            spk1_result["invalidated"] += invalidated
            if person_id:
                confirmed = si.confirm_sample(
                    session, meeting_id, cluster_id, person_id,
                )
                if confirmed is not None:
                    spk1_result["confirmed"] += 1

    return {
        "updated": updated,
        "mapping": mapping,
        "speakers": seen_labels,
        "spk1": spk1_result,
    }


@router.get("/{meeting_id}/analytics", response_model=TalkTimeAnalyticsResponse)
def get_meeting_analytics(
    meeting_id: str,
    config: Annotated[AppConfig, Depends(get_config)],
    storage: Annotated[StorageEngine, Depends(get_storage)],
):
    """Get talk-time analytics for a meeting."""
    from meeting_minutes.analytics import compute_talk_time_analytics

    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    data_dir = Path(config.data_dir).expanduser()
    transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"

    analytics = compute_talk_time_analytics(transcript_path)
    if analytics is None:
        raise HTTPException(status_code=404, detail="No transcript data available for analytics")

    return TalkTimeAnalyticsResponse(
        total_duration_seconds=analytics.total_duration_seconds,
        speakers=[
            {
                "speaker": sa.speaker,
                "talk_time_seconds": sa.talk_time_seconds,
                "talk_time_percentage": sa.talk_time_percentage,
                "segment_count": sa.segment_count,
                "question_count": sa.question_count,
                "monologues": sa.monologues,
            }
            for sa in analytics.speakers
        ],
        has_diarization=analytics.has_diarization,
    )


@router.get("/{meeting_id}/audio")
def get_audio(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Stream the audio file for a meeting."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    if m.transcript is None or not m.transcript.audio_file_path:
        raise HTTPException(status_code=404, detail="No audio file available for this meeting")

    audio_path = Path(m.transcript.audio_file_path).resolve()

    # Prevent path traversal: audio must be inside the configured data directory
    audio_root = Path(config.data_dir).expanduser().resolve()
    if not str(audio_path).startswith(str(audio_root) + "/"):
        raise HTTPException(status_code=403, detail="Audio file path not allowed")

    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    media_type = "audio/flac"
    if audio_path.suffix == ".wav":
        media_type = "audio/wav"
    elif audio_path.suffix == ".mp3":
        media_type = "audio/mpeg"
    elif audio_path.suffix in (".m4a", ".mp4"):
        media_type = "audio/mp4"

    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        filename=audio_path.name,
    )


@router.patch("/{meeting_id}", response_model=MeetingDetail)
def update_meeting(
    meeting_id: str,
    body: MeetingUpdate,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    session: Annotated[Session, Depends(get_db_session)],
):
    """Update meeting metadata (status, tags)."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    if body.status is not None:
        m.status = body.status

    session.commit()
    session.refresh(m)
    return _meeting_to_detail(m)


@router.delete("/{meeting_id}")
def delete_meeting(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    search_engine: Annotated[SearchEngine, Depends(get_search)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Delete a meeting and all associated data: DB, search index, files, and Obsidian."""
    import json as _json

    # 1. Get meeting info before deleting (needed for Obsidian file lookup)
    meeting = storage.get_meeting(meeting_id)
    minutes_title = None
    minutes_date = None
    if meeting and meeting.minutes:
        # Read minutes JSON to get the title and date for Obsidian file
        data_dir = Path(config.data_dir).expanduser()
        minutes_json_path = data_dir / "minutes" / f"{meeting_id}.json"
        if minutes_json_path.exists():
            try:
                mdata = _json.loads(minutes_json_path.read_text())
                metadata = mdata.get("metadata", {})
                minutes_title = metadata.get("title", "")
                minutes_date = metadata.get("date", "")
            except Exception:
                pass

    # 2. Delete from search index
    search_engine.remove_from_index(meeting_id)

    # 3. Delete from database (cascades to transcript, minutes, actions, decisions)
    ok = storage.delete_meeting(meeting_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    # 4. Delete files from disk
    data_dir = Path(config.data_dir).expanduser()
    deleted_files = []
    for subdir, pattern in [
        ("recordings", f"{meeting_id}.*"),
        ("transcripts", f"{meeting_id}.json"),
        ("minutes", f"{meeting_id}.json"),
        ("minutes", f"{meeting_id}.md"),
    ]:
        folder = data_dir / subdir
        for f in folder.glob(pattern):
            try:
                f.unlink()
                deleted_files.append(f.name)
            except Exception:
                pass

    # 5. Delete Obsidian file if export is enabled
    if config.obsidian.enabled and config.obsidian.vault_path and minutes_title and minutes_date:
        try:
            from datetime import datetime as dt
            vault_path = Path(config.obsidian.vault_path).expanduser()
            date_obj = dt.fromisoformat(minutes_date.split("T")[0])
            year = date_obj.strftime("%Y")
            year_month = date_obj.strftime("%Y-%m")
            date_str = date_obj.strftime("%Y-%m-%d")

            # Build the expected filename
            safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in minutes_title).strip()[:80]
            obsidian_file = vault_path / "Meeting Minutes" / year / year_month / f"{date_str} {safe_title}.md"

            if obsidian_file.exists():
                obsidian_file.unlink()
                deleted_files.append(f"obsidian:{obsidian_file.name}")
        except Exception:
            pass  # Non-fatal — Obsidian cleanup is best-effort

    print(f"  Deleted meeting {meeting_id}: DB + {len(deleted_files)} files ({', '.join(deleted_files)})")
    return {"status": "deleted", "meeting_id": meeting_id, "files_deleted": len(deleted_files)}


@router.post("/{meeting_id}/regenerate", dependencies=[Depends(check_llm_limit)])
async def regenerate_meeting(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Re-run minutes generation for a meeting."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    from meeting_minutes.pipeline import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(config)
    try:
        await orchestrator.reprocess(meeting_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Regeneration failed: {exc}")

    return {"status": "regenerated", "meeting_id": meeting_id}


@router.post("/{meeting_id}/export")
def export_meeting(
    meeting_id: str,
    body: ExportRequest,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Export meeting minutes in the requested format."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    if body.format not in ("pdf", "md"):
        raise HTTPException(status_code=400, detail="Unsupported format. Use 'pdf' or 'md'.")

    if m.minutes is None or not m.minutes.markdown_content:
        raise HTTPException(status_code=400, detail="No minutes to export for this meeting")

    if body.format == "md":
        data_dir = Path(config.data_dir).expanduser()
        export_dir = data_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{meeting_id}.md"
        export_path = export_dir / filename
        export_path.write_text(m.minutes.markdown_content, encoding="utf-8")
        return FileResponse(
            path=str(export_path),
            media_type="text/markdown",
            filename=filename,
        )

    # PDF export — basic placeholder that returns markdown wrapped in a note
    # A full PDF implementation would use a library like weasyprint or reportlab.
    raise HTTPException(
        status_code=501,
        detail="PDF export is not yet implemented. Use 'md' format instead.",
    )
