"""ZIP-bundle helper for series exports (EXP-1)."""

from __future__ import annotations

import io
import zipfile
from typing import Iterable

from meeting_minutes.export import ExportResult


def make_zip(items: Iterable[ExportResult]) -> bytes:
    """Pack the given exports into a single in-memory ``.zip`` archive.

    Duplicate filenames get disambiguated with an incrementing suffix so
    two meetings with identical titles on the same day don't collide.
    """
    buf = io.BytesIO()
    seen: dict[str, int] = {}
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            name = item.filename
            if name in seen:
                seen[name] += 1
                stem, _, ext = name.rpartition(".")
                name = f"{stem or name}-{seen[name]}.{ext}" if ext else f"{name}-{seen[name]}"
            else:
                seen[name] = 0
            zf.writestr(name, item.content)
    return buf.getvalue()
