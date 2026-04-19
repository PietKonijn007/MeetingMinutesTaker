"""Onboarding diagnostic endpoint (ONB-1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from meeting_minutes.api.deps import get_config
from meeting_minutes.config import AppConfig

router = APIRouter(prefix="/api/doctor", tags=["doctor"])


def _aggregate_status(checks) -> str:
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "ok"


@router.get("")
def run_doctor(config: Annotated[AppConfig, Depends(get_config)]):
    """Run the ten ONB-1 checks and return the JSON result."""
    from meeting_minutes.doctor import run_checks

    results = run_checks(config)
    return {
        "checks": [r.to_dict() for r in results],
        "overall_status": _aggregate_status(results),
    }


@router.get("/{name}")
def run_single_check(
    name: str,
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Re-run a single named check. Used by the /onboarding page retry button."""
    from meeting_minutes.doctor import run_checks

    for result in run_checks(config):
        if result.name == name:
            return result.to_dict()
    return {"error": f"Unknown check: {name}"}
