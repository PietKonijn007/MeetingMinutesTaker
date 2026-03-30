"""Search endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from meeting_minutes.api.deps import get_search
from meeting_minutes.api.schemas import PaginatedResponse, SearchResultItem
from meeting_minutes.system3.search import SearchEngine

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=PaginatedResponse)
def search_meetings(
    search_engine: Annotated[SearchEngine, Depends(get_search)],
    q: str = Query(..., min_length=1, description="Search query"),
    type: Optional[str] = Query(None, description="Meeting type filter"),
    after: Optional[str] = Query(None, description="After date (ISO)"),
    before: Optional[str] = Query(None, description="Before date (ISO)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Full-text search across meetings."""
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

    items = [
        SearchResultItem(
            meeting_id=r.meeting_id,
            title=r.title,
            date=r.date.isoformat() if r.date else None,
            meeting_type=r.meeting_type,
            snippet=r.snippet,
        ).model_dump()
        for r in results.results
    ]

    return PaginatedResponse(
        items=items,
        total=results.total_count,
        limit=limit,
        offset=offset,
    )
