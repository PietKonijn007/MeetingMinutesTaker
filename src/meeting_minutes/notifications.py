"""Desktop notifications for pipeline events (NOT-1).

Thin wrapper over ``pync`` (macOS only) with a safe no-op fallback on
Linux/Windows or when ``pync`` is not installed. Notifications fire on
pipeline ``complete`` and ``failed`` transitions — see the calls in
``pipeline.py``.

The module is intentionally self-contained: no exceptions escape to the
pipeline, no heavy imports happen on module load, and the pync import is
deferred so a missing native dependency can't break ``mm serve`` startup
on non-macOS hosts.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from meeting_minutes.config import NotificationsConfig


logger = logging.getLogger(__name__)

# Module-level guard so the "pync missing" INFO log only fires once per process.
_pync_missing_logged = False


def _resolve_config(config: "NotificationsConfig | None" = None) -> "NotificationsConfig":
    """Return the active notifications config. Falls back to defaults when
    the caller didn't pass one through (e.g. pipeline hooks don't thread
    the full AppConfig around for every stage transition)."""
    if config is not None:
        return config
    try:
        from meeting_minutes.config import ConfigLoader

        return ConfigLoader.load_default().notifications
    except Exception:  # pragma: no cover - config load shouldn't fail here
        from meeting_minutes.config import NotificationsConfig

        return NotificationsConfig()


def _get_pync():
    """Lazy pync import. Returns ``None`` if unavailable or not on macOS.

    Logs a single INFO line the first time it's missing so it's obvious in
    logs without spamming.
    """
    global _pync_missing_logged
    if sys.platform != "darwin":
        return None
    try:
        import pync  # type: ignore
        return pync
    except ImportError:
        if not _pync_missing_logged:
            logger.info(
                "pync is not installed — desktop notifications disabled. "
                "Install with: pip install pync"
            )
            _pync_missing_logged = True
        return None


def _notify(
    title: str,
    message: str,
    *,
    meeting_id: str,
    config: "NotificationsConfig | None" = None,
) -> bool:
    """Send a desktop notification. Returns ``True`` on successful dispatch,
    ``False`` if disabled, unavailable, or on error (never raises).
    """
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return False

    pync = _get_pync()
    if pync is None:
        return False

    try:
        kwargs = {
            "title": title,
            "open": f"{cfg.click_url_base.rstrip('/')}/{meeting_id}",
        }
        if cfg.sound:
            kwargs["sound"] = "default"
        pync.Notifier.notify(message, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 - notifications must never raise
        logger.debug("Notification dispatch failed: %s", exc)
        return False


def notify_pipeline_complete(
    meeting_id: str,
    title: str,
    duration: str | None = None,
    action_count: int | None = None,
    *,
    config: "NotificationsConfig | None" = None,
) -> bool:
    """Fire a "meeting ready" notification."""
    body_parts: list[str] = []
    if duration:
        body_parts.append(duration)
    if action_count is not None:
        suffix = "item" if action_count == 1 else "items"
        body_parts.append(f"{action_count} action {suffix}")
    body = " · ".join(body_parts) if body_parts else "Processing complete."
    return _notify(
        title=f"Meeting ready: {title}" if title else "Meeting ready",
        message=body,
        meeting_id=meeting_id,
        config=config,
    )


def notify_pipeline_failed(
    meeting_id: str,
    title: str,
    stage: str,
    error: str,
    *,
    config: "NotificationsConfig | None" = None,
) -> bool:
    """Fire a "pipeline failed" notification."""
    # Keep the body short — macOS truncates long notification bodies.
    short_error = (error or "").strip()
    if len(short_error) > 160:
        short_error = short_error[:157] + "…"
    body = f"{stage}: {short_error}" if short_error else f"Stage {stage} failed."
    return _notify(
        title=f"Pipeline failed: {title}" if title else "Pipeline failed",
        message=body,
        meeting_id=meeting_id,
        config=config,
    )
