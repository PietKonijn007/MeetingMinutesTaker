"""Per-meeting attachments — files, links, images.

See ``specs/09-attachments.md``. This package is split across:

- :mod:`storage` — DB row + filesystem operations.
- :mod:`sidecar` — read/write the per-attachment markdown sidecar
  (``data/attachments/{meeting_id}/{attachment_id}.md``) that holds the
  extracted text and (in a follow-up batch) the LLM-generated summary.
- :mod:`extractors` — kind/mime-aware text extraction. PDF text-layer is
  shipped in this batch; OCR, DOCX, PPTX, link-fetch follow.
- :mod:`worker` — async pipeline ``extract → write sidecar``. The
  summarization step + pipeline injection land in the next batch.

The public surface is intentionally small: callers go through
:func:`storage.add_file`, :func:`storage.list_for_meeting`,
:func:`storage.delete`, and :func:`storage.get`.
"""

from __future__ import annotations

from meeting_minutes.attachments import sidecar, storage, worker
from meeting_minutes.attachments.sidecar import (
    parse_attachment_sidecar,
    write_attachment_sidecar,
)
from meeting_minutes.attachments.storage import (
    add_file,
    attachment_dir,
    delete,
    get,
    list_for_meeting,
)

__all__ = [
    "add_file",
    "attachment_dir",
    "delete",
    "get",
    "list_for_meeting",
    "parse_attachment_sidecar",
    "sidecar",
    "storage",
    "worker",
    "write_attachment_sidecar",
]
