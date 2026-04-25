"""Meeting endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session, get_search, get_storage
from meeting_minutes.api.rate_limit import check_llm_limit
from meeting_minutes.api.schemas import (
    ActionItemResponse,
    DecisionResponse,
    ErrorResponse,
    ExportRequest,
    ExternalNotesRequest,
    MeetingDetail,
    MeetingListItem,
    MeetingTypeChangeRequest,
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

        detailed_notes = ""
        if structured:
            sentiment = structured.get("sentiment")
            discussion_points = structured.get("discussion_points", []) or []
            risks_and_concerns = structured.get("risks_and_concerns", []) or []
            follow_ups = structured.get("follow_ups", []) or []
            parking_lot = structured.get("parking_lot", []) or []
            key_topics = structured.get("key_topics", []) or []
            detailed_notes = structured.get("detailed_notes") or ""

        # detailed_notes may live at the top level of the on-disk MinutesJSON
        # (new persistence path); fall back to that if structured_data didn't have it.
        if not detailed_notes and file_data:
            detailed_notes = file_data.get("detailed_notes") or ""

        minutes = MinutesResponse(
            minutes_id=m.minutes.minutes_id,
            summary=m.minutes.summary,
            detailed_notes=detailed_notes or None,
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

    # Round-trip the post-hoc external notes (if any) so the "External
    # notes" tab can preload the last paste and surface the background
    # job's state. All three fields are best-effort — if the sidecar is
    # missing or malformed we simply return null.
    external_notes_text: str | None = None
    external_notes_status: str | None = None
    external_notes_error: str | None = None
    regen_status: str | None = None
    regen_error: str | None = None
    try:
        from meeting_minutes import external_notes as _ext
        from meeting_minutes.config import ConfigLoader as _ConfigLoader
        _cfg = _ConfigLoader.load_default()
        _data_dir = Path(_cfg.data_dir).expanduser()
        _sidecar = _ext.load_notes_sidecar(_data_dir, m.meeting_id)
        external_notes_text = _sidecar.get("external_notes") or None
        external_notes_status = _sidecar.get("external_notes_status") or None
        external_notes_error = _sidecar.get("external_notes_error") or None
        regen_status = _sidecar.get("regen_status") or None
        regen_error = _sidecar.get("regen_error") or None
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
        external_notes=external_notes_text,
        external_notes_status=external_notes_status,
        external_notes_error=external_notes_error,
        regen_status=regen_status,
        regen_error=regen_error,
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


def apply_speaker_mapping(
    data_dir: Path,
    meeting_id: str,
    mapping: dict[str, str],
) -> tuple[int, list[str]]:
    """Rewrite segment speaker labels + the top-level ``speakers`` array.

    Shared by ``PATCH /transcript/speakers`` (user-driven rename) and the
    external-notes background job (LLM-driven rename). Idempotent: labels not
    present in ``mapping`` are left alone; labels already renamed to a human
    name are untouched when the caller filters the mapping down to
    ``SPEAKER_\\d+`` keys.

    Also updates ``data/notes/{meeting_id}.json`` so subsequent regenerations
    pick up the new names (the pipeline pulls ``user_speakers`` from there).

    Returns ``(segments_updated, final_label_order)``.
    """
    import json as _json

    transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"
    if not transcript_path.exists():
        raise FileNotFoundError(
            f"No transcript JSON on disk for meeting {meeting_id}: {transcript_path}"
        )

    data = _json.loads(transcript_path.read_text())
    segments = data.get("transcript", {}).get("segments", []) or []

    if not mapping:
        # Still recompute the speakers array from current segments — cheap
        # and keeps the return shape consistent.
        seen: list[str] = []
        for seg in segments:
            spk = seg.get("speaker")
            if spk and spk not in seen:
                seen.append(spk)
        return 0, seen

    # Rewrite segment speakers.
    updated = 0
    for seg in segments:
        if seg.get("speaker") in mapping:
            seg["speaker"] = mapping[seg["speaker"]]
            updated += 1

    # Rebuild speakers array preserving first-appearance order from segments.
    seen_labels: list[str] = []
    for seg in segments:
        spk = seg.get("speaker")
        if spk and spk not in seen_labels:
            seen_labels.append(spk)
    data["speakers"] = [
        {"label": l, "name": None, "email": None, "confidence": 0.0}
        for l in seen_labels
    ]
    data["transcript"]["segments"] = segments

    transcript_path.write_text(_json.dumps(data, indent=2, default=str))

    # Update the notes file so reprocess/regenerate use these names.
    notes_dir = data_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes_file = notes_dir / f"{meeting_id}.json"
    notes_data: dict = {}
    if notes_file.exists():
        try:
            notes_data = _json.loads(notes_file.read_text())
        except Exception:
            notes_data = {}
    notes_data["speakers"] = seen_labels
    notes_file.write_text(_json.dumps(notes_data, indent=2))

    return updated, seen_labels


@router.patch("/{meeting_id}/transcript/speakers")
async def update_transcript_speakers(
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

    Optional SPK-1 field to bind the voice sample to a specific person:
        { "mapping": {...}, "person_mapping": {"SPEAKER_00": "p-jon"} }

    Optional flag — defaults to ``True`` — to schedule an async minutes regen
    after the rename is applied:
        { "mapping": {...}, "regenerate": true }

    Person resolution (when ``person_mapping`` is missing or partial):
      - If a name in ``mapping`` matches an existing ``persons.name`` (case
        insensitive), that person's ID is used and the voice sample is
        confirmed under them. This is the same effect as if the user had
        picked the suggestion in the UI.
      - If no match, a new ``persons`` row is created with that name and the
        voice sample is confirmed under the new person. Closes the
        previously-orphaned-voice-sample gap for free-form typing.

    For each (cluster_id → person_id) pair the endpoint runs:
      ``invalidate_contamination`` (demotes prior samples for that cluster
      under a *different* person to ``confirmed=False``) followed by
      ``confirm_sample`` (flips the new pair's sample to ``confirmed=True``).

    Also updates data/notes/{meeting_id}.json so the names persist for
    regenerate/reprocess. When ``regenerate=true`` the endpoint sets
    ``regen_status=processing`` on the sidecar and schedules a fire-and-forget
    asyncio task; callers should poll ``GET /meetings/{id}`` for
    ``regen_status`` to flip to ``ready`` (or ``error``).
    """
    import json as _json
    import uuid as _uuid

    from meeting_minutes import external_notes as ext_mod
    from meeting_minutes import regen as regen_mod
    from meeting_minutes.system3.db import PersonORM

    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    data_dir = Path(config.data_dir).expanduser()
    transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="No transcript JSON on disk")

    # Build mapping from request body. Only the mapping construction lives
    # here; the rewrite itself is delegated to ``apply_speaker_mapping`` so
    # the background external-notes job can share the same code path.
    mapping: dict[str, str] = {}
    if body.get("mapping"):
        mapping = {k: v.strip() for k, v in body["mapping"].items() if v and v.strip()}
    elif body.get("ordered_names"):
        ordered = body["ordered_names"]
        if isinstance(ordered, str):
            ordered = [n.strip() for n in ordered.split(",") if n.strip()]
        # First-appearance order from segments — need to peek at the current
        # transcript here (pre-rewrite) to pair ordered names with the
        # correct cluster ids.
        data = _json.loads(transcript_path.read_text())
        segments = data.get("transcript", {}).get("segments", []) or []
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

    updated, seen_labels = apply_speaker_mapping(data_dir, meeting_id, mapping)

    # SPK-1: bind each cluster to a person and (in)validate voice samples.
    #
    # The caller may pass an explicit ``person_mapping``. For any cluster not
    # covered there, we fall back to name resolution: case-insensitive match
    # against ``persons.name``, else create a new Person on the fly. This
    # closes the previously-orphaned-sample gap when the user just types a
    # free-form name into the rename editor.
    from meeting_minutes.system1 import speaker_identity as si

    explicit_person_mapping = body.get("person_mapping") or {}
    if not isinstance(explicit_person_mapping, dict):
        explicit_person_mapping = {}

    spk1_result: dict = {"confirmed": 0, "invalidated": 0, "created_persons": []}
    resolved_person_mapping: dict[str, str] = {}

    for cluster_id, name in mapping.items():
        if not cluster_id or not name:
            continue
        person_id = explicit_person_mapping.get(cluster_id) or ""
        if not person_id:
            # Try to resolve by case-insensitive name match.
            existing = (
                session.query(PersonORM)
                .filter(PersonORM.name.ilike(name))
                .first()
            )
            if existing is not None:
                person_id = existing.person_id
            else:
                # Create a new person on the fly. Email is unknown — leaving
                # it null is fine; the user can fill it in later from the
                # People page.
                created = PersonORM(
                    person_id=f"p-{_uuid.uuid4().hex[:8]}",
                    name=name,
                    email=None,
                )
                session.add(created)
                session.flush()  # we need created.person_id below
                person_id = created.person_id
                spk1_result["created_persons"].append(
                    {"person_id": person_id, "name": name}
                )
        resolved_person_mapping[cluster_id] = person_id

        # Demote any sample previously written for this cluster under a
        # different person, then confirm the new pair.
        invalidated = si.invalidate_contamination(
            session, meeting_id, cluster_id, person_id,
        )
        spk1_result["invalidated"] += invalidated
        confirmed = si.confirm_sample(
            session, meeting_id, cluster_id, person_id,
        )
        if confirmed is not None:
            spk1_result["confirmed"] += 1

    if spk1_result["created_persons"]:
        session.commit()

    # ----- Async regeneration (default on) -----------------------------
    #
    # The frontend used to call ``POST /regenerate`` synchronously after
    # this PATCH; we now do it server-side so the browser doesn't block
    # for 15-60s of LLM time. Caller can opt out with ``regenerate=false``
    # (matches the existing "Save only" button in the speaker editor).
    regenerate = bool(body.get("regenerate", True))
    regen_status: str | None = None
    if regenerate:
        sidecar = ext_mod.load_notes_sidecar(data_dir, meeting_id)
        if sidecar.get("regen_status") == regen_mod.STATUS_PROCESSING:
            # Don't double-fire. The rename + sample-confirm work above is
            # already committed to disk and DB; the in-flight regen will
            # pick it up on its next pass. Surface that to the caller so
            # they don't trigger their own poll twice.
            regen_status = regen_mod.STATUS_PROCESSING
        else:
            sidecar["regen_status"] = regen_mod.STATUS_PROCESSING
            sidecar.pop("regen_error", None)
            ext_mod.write_notes_sidecar(data_dir, meeting_id, sidecar)
            # The endpoint is ``async def``, so we're already on the event
            # loop — same path as ``change_meeting_type`` /
            # ``submit_external_notes``. The session.query / add / commit
            # calls above run synchronously on the loop; for SQLite + single
            # user that's microseconds, not a real concern.
            regen_mod.schedule_background_regen(config, meeting_id)
            regen_status = regen_mod.STATUS_PROCESSING

    return {
        "updated": updated,
        "mapping": mapping,
        "speakers": seen_labels,
        "spk1": spk1_result,
        "person_mapping": resolved_person_mapping,
        "regen_status": regen_status,
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
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Update meeting metadata (status, tags, title).

    A title change rewrites the embedded title inside the on-disk minutes
    JSON + Markdown, refreshes the FTS index, mirrors the new title to the
    notes sidecar (so a future regeneration won't overwrite it), and renames
    the Obsidian export from ``{date} {old_safe_title}.md`` to ``{date}
    {new_safe_title}.md``. The internal data files under ``recordings/``,
    ``transcripts/``, ``minutes/`` and ``notes/`` are keyed by ``meeting_id``
    (UUID) and are NOT renamed — only their contents are touched.
    """
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    if body.status is not None:
        m.status = body.status

    if body.title is not None:
        new_title = body.title.strip()
        if not new_title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        if len(new_title) > 200:
            raise HTTPException(status_code=400, detail="Title too long (max 200 chars)")

        old_title = m.title or ""
        if new_title != old_title:
            m.title = new_title

            new_md = _sync_meeting_title_files(
                meeting_id=meeting_id,
                new_title=new_title,
                old_title=old_title,
                config=config,
            )
            # Keep the DB-cached markdown in sync with the on-disk file so
            # the API's `minutes.markdown_content` reflects the new heading.
            if new_md is not None and m.minutes is not None:
                m.minutes.markdown_content = new_md

            # Refresh FTS index so search hits use the new title.
            from sqlalchemy import text
            transcript_text = m.transcript.full_text if m.transcript else ""
            minutes_text = m.minutes.markdown_content if m.minutes else ""
            session.execute(
                text("DELETE FROM meetings_fts WHERE meeting_id = :mid"),
                {"mid": meeting_id},
            )
            session.execute(
                text(
                    "INSERT INTO meetings_fts(meeting_id, title, transcript_text, minutes_text) "
                    "VALUES (:mid, :title, :tt, :mt)"
                ),
                {
                    "mid": meeting_id,
                    "title": new_title,
                    "tt": transcript_text or "",
                    "mt": minutes_text or "",
                },
            )

    session.commit()
    session.refresh(m)
    return _meeting_to_detail(m)


def _safe_obsidian_title(title: str) -> str:
    """Filename-safe title used by the Obsidian exporter and matched here
    when we need to delete the previous export. Kept in lockstep with
    :func:`meeting_minutes.obsidian.export_to_obsidian`."""
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()[:80]


def _sync_meeting_title_files(
    meeting_id: str,
    new_title: str,
    old_title: str,
    config: AppConfig,
) -> str | None:
    """Rewrite the title in the on-disk minutes JSON + MD, mirror the new
    title to the notes sidecar (for future regenerations), and rename the
    Obsidian export.

    Returns the updated markdown content (so callers can refresh the DB
    cache), or ``None`` if no markdown file existed.
    """
    import json as _json

    data_dir = Path(config.data_dir).expanduser()
    minutes_dir = data_dir / "minutes"
    json_path = minutes_dir / f"{meeting_id}.json"
    md_path = minutes_dir / f"{meeting_id}.md"
    notes_path = data_dir / "notes" / f"{meeting_id}.json"

    enc_key = (
        config.security.encryption_key
        if config.security.encryption_enabled
        else None
    )

    minutes_date: str | None = None
    new_md_content: str | None = None

    # 1. Minutes JSON: bump metadata.title in place.
    if json_path.exists():
        try:
            if enc_key:
                from meeting_minutes.encryption import decrypt_file_text, encrypt_file
                raw = decrypt_file_text(json_path, enc_key)
            else:
                raw = json_path.read_text(encoding="utf-8")
            data = _json.loads(raw)
            metadata = data.get("metadata") or {}
            metadata["title"] = new_title
            data["metadata"] = metadata
            minutes_date = metadata.get("date")
            json_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
            if enc_key:
                from meeting_minutes.encryption import encrypt_file
                encrypt_file(json_path, enc_key)
        except Exception as exc:
            print(f"  ⚠ Failed to update minutes JSON title for {meeting_id}: {exc}")

    # 2. Minutes Markdown: replace the first `# ...` heading.
    if md_path.exists():
        try:
            if enc_key:
                from meeting_minutes.encryption import decrypt_file_text, encrypt_file
                content = decrypt_file_text(md_path, enc_key)
            else:
                content = md_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("# "):
                    lines[i] = f"# {new_title}"
                    break
            new_md_content = "\n".join(lines)
            md_path.write_text(new_md_content, encoding="utf-8")
            if enc_key:
                encrypt_file(md_path, enc_key)
        except Exception as exc:
            print(f"  ⚠ Failed to update minutes MD title for {meeting_id}: {exc}")

    # 3. Notes sidecar: mirror the title so a future regeneration (e.g. a
    # meeting-type change) re-uses it instead of letting the LLM invent a
    # new one. Best-effort — a missing/unreadable sidecar is fine.
    try:
        notes_data: dict = {}
        if notes_path.exists():
            try:
                notes_data = _json.loads(notes_path.read_text())
                if not isinstance(notes_data, dict):
                    notes_data = {}
            except Exception:
                notes_data = {}
        notes_data["title"] = new_title
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        notes_path.write_text(_json.dumps(notes_data, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"  ⚠ Failed to mirror title into notes sidecar for {meeting_id}: {exc}")

    # 4. Obsidian: delete the file with the old safe-title, then re-export.
    if config.obsidian.enabled and config.obsidian.vault_path and minutes_date:
        try:
            vault_path = Path(config.obsidian.vault_path).expanduser()
            try:
                date_obj = datetime.fromisoformat(minutes_date.split("T")[0])
            except Exception:
                date_obj = None
            if date_obj is not None:
                year = date_obj.strftime("%Y")
                year_month = date_obj.strftime("%Y-%m")
                date_str = date_obj.strftime("%Y-%m-%d")
                if old_title:
                    old_obsidian_file = (
                        vault_path / "Meeting Minutes" / year / year_month
                        / f"{date_str} {_safe_obsidian_title(old_title)}.md"
                    )
                    if old_obsidian_file.exists():
                        new_obsidian_file = (
                            vault_path / "Meeting Minutes" / year / year_month
                            / f"{date_str} {_safe_obsidian_title(new_title)}.md"
                        )
                        if old_obsidian_file != new_obsidian_file:
                            try:
                                old_obsidian_file.unlink()
                            except Exception:
                                pass

            # Re-export reads the freshly-updated JSON for the new title.
            from meeting_minutes.pipeline import PipelineOrchestrator
            orchestrator = PipelineOrchestrator(config)
            orchestrator._export_to_obsidian_from_file(meeting_id)
        except Exception as exc:
            print(f"  ⚠ Obsidian rename failed for {meeting_id}: {exc}")

    return new_md_content


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


@router.post(
    "/{meeting_id}/external-notes",
    dependencies=[Depends(check_llm_limit)],
    status_code=202,
)
async def submit_external_notes(
    meeting_id: str,
    body: ExternalNotesRequest,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Accept pasted notes from an external meeting app and trigger post-hoc updates.

    Freeform text in; archived + fed back into minutes generation. The
    endpoint returns immediately (202 Accepted) — the speaker rename and
    minutes regeneration run as a background task because they involve LLM
    calls that can take 10–60s. Progress is surfaced via
    ``external_notes_status`` on ``GET /meetings/{id}`` — callers should
    poll until it flips from ``processing`` to ``ready`` (or ``error``).

    Happens synchronously before returning:
      1. Validates the meeting and its transcript exist.
      2. Writes the raw paste to ``data/external_notes/{id}.md`` (overwrites
         any prior paste — single-user MVP).
      3. Merges the text into ``data/notes/{id}.json`` under ``external_notes``
         and flips ``external_notes_status`` to ``processing``.

    Happens in the background (fire-and-forget asyncio task):
      4. LLM-infers ``SPEAKER_xx → Name`` mappings from the notes.
      5. Applies the mappings to the transcript JSON.
      6. Runs ``PipelineOrchestrator.reprocess`` (regenerates summary,
         re-ingests DB, re-exports Obsidian).
      7. Appends the verbatim paste as a ``## External notes`` section to
         the rendered markdown (local ``.md``, minutes JSON, DB column,
         Obsidian) so it survives future regenerations.
    """
    from meeting_minutes import external_notes as ext

    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="External notes text is empty")

    data_dir = Path(config.data_dir).expanduser()
    transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"
    if not transcript_path.exists():
        raise HTTPException(
            status_code=400,
            detail="Meeting has no transcript — external notes can only be added post-hoc",
        )

    # --- Sync portion: archive + sidecar + status flip -----------------
    ext.write_archive(data_dir, meeting_id, text)
    sidecar = ext.load_notes_sidecar(data_dir, meeting_id)
    sidecar["external_notes"] = text
    sidecar["external_notes_status"] = ext.STATUS_PROCESSING
    sidecar.pop("external_notes_error", None)
    ext.write_notes_sidecar(data_dir, meeting_id, sidecar)

    # --- Async portion: rename speakers + reprocess + re-append --------
    ext.schedule_background_update(config, meeting_id, text)

    return {
        "status": "accepted",
        "meeting_id": meeting_id,
        "external_notes_status": ext.STATUS_PROCESSING,
    }


@router.post(
    "/{meeting_id}/meeting-type",
    dependencies=[Depends(check_llm_limit)],
    status_code=202,
)
async def change_meeting_type(
    meeting_id: str,
    body: MeetingTypeChangeRequest,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Switch a meeting's type and rebuild its summary against the new template.

    Returns 202 immediately — reprocess takes 15-60s of LLM time so it runs
    as a fire-and-forget asyncio task. Progress is surfaced via
    ``meeting_type_status`` on ``GET /meetings/{id}`` — callers should poll
    until it flips from ``processing`` to ``ready`` (or ``error``).

    Synchronous validation:
      1. Meeting exists.
      2. Requested type parses cleanly into ``MeetingType``.
      3. Transcript file exists (can't regenerate without one).
      4. Requested type differs from the current type (``/regenerate`` is the
         right tool for re-running with the same type).
      5. No retype already in flight.

    Synchronous side effects before returning 202:
      - Update ``data/notes/{id}.json``: set ``meeting_type`` to the new
        value and ``regen_status`` to ``processing``. The pipeline reads
        ``meeting_type`` from the sidecar at the start of the next
        ``run_generation``, so the new value is automatically picked up by
        the background task.

    Async (fire-and-forget):
      - ``PipelineOrchestrator.reprocess`` rebuilds minutes against the new
        template, re-ingests the DB, re-exports Obsidian.
      - If external notes were previously pasted, replays the
        ``## External notes`` post-append so the verbatim paste survives.
    """
    from meeting_minutes import external_notes as ext
    from meeting_minutes import regen
    from meeting_minutes.models import MeetingType

    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    # Validate the requested type against the enum.
    raw = (body.meeting_type or "").strip()
    try:
        new_type = MeetingType(raw).value
    except ValueError:
        valid = ", ".join(t.value for t in MeetingType)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid meeting_type '{raw}'. Valid values: {valid}",
        )

    data_dir = Path(config.data_dir).expanduser()
    transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"
    if not transcript_path.exists():
        raise HTTPException(
            status_code=400,
            detail="Meeting has no transcript — type can only be changed post-hoc",
        )

    # No-op if the type isn't actually changing — point the user at /regenerate.
    current_type = (m.meeting_type or "").strip()
    if current_type == new_type:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Meeting is already type '{new_type}'. "
                "Use POST /meetings/{id}/regenerate to rerun without changing the type."
            ),
        )

    # Don't double-fire: if any regen (type change, speaker rename, …) is
    # still in flight, refuse — running two reprocesses in parallel would
    # race for the same on-disk minutes.
    sidecar = ext.load_notes_sidecar(data_dir, meeting_id)
    if sidecar.get("regen_status") == regen.STATUS_PROCESSING:
        raise HTTPException(
            status_code=409,
            detail="A regeneration is already in progress for this meeting",
        )

    # --- Sync portion: write sidecar + flip status ---------------------
    sidecar["meeting_type"] = new_type
    sidecar["regen_status"] = regen.STATUS_PROCESSING
    sidecar.pop("regen_error", None)
    ext.write_notes_sidecar(data_dir, meeting_id, sidecar)

    # --- Async portion: reprocess + replay external notes --------------
    regen.schedule_background_regen(config, meeting_id)

    return {
        "status": "accepted",
        "meeting_id": meeting_id,
        "meeting_type": new_type,
        "regen_status": regen.STATUS_PROCESSING,
    }


def _export_meeting_to_response(
    m: MeetingORM,
    *,
    format: str,
    with_transcript: bool,
) -> Response:
    """Shared helper used by both the POST and GET export endpoints.

    Delegates rendering to ``meeting_minutes.export.export`` and wraps the
    result in a ``Response`` with an ``attachment`` Content-Disposition.
    Converts ``ExportDependencyMissing`` into a 501.
    """
    from meeting_minutes.export import ExportDependencyMissing, export as render_export

    if format not in ("pdf", "md", "docx"):
        raise HTTPException(
            status_code=400,
            detail="Unsupported format. Use 'pdf', 'docx' or 'md'.",
        )
    if m.minutes is None or not (m.minutes.markdown_content or "").strip():
        raise HTTPException(status_code=400, detail="No minutes to export for this meeting")

    try:
        result = render_export(m, format=format, with_transcript=with_transcript)
    except ExportDependencyMissing as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.post("/{meeting_id}/export")
def export_meeting(
    meeting_id: str,
    body: ExportRequest,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Export meeting minutes in the requested format (POST — legacy/body shape)."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    return _export_meeting_to_response(
        m, format=body.format, with_transcript=False,
    )


@router.get("/{meeting_id}/export")
def export_meeting_get(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    format: str = Query("md"),
    with_transcript: bool = Query(False),
):
    """Export meeting minutes in the requested format (GET — browser download)."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    return _export_meeting_to_response(
        m, format=format, with_transcript=with_transcript,
    )
