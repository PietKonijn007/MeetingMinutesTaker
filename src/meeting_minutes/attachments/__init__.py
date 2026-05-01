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

from meeting_minutes.attachments import (
    pipeline_integration,
    sidecar,
    storage,
    summarizer,
    worker,
)
from meeting_minutes.attachments.pipeline_integration import (
    AttachmentEntry,
    append_attachments_section_to_files,
    gather_entries,
    render_llm_context_block,
    wait_for_pending,
)
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
from meeting_minutes.attachments.summarizer import (
    SummaryRequest,
    SummaryResult,
    SummaryTier,
    pick_tier,
    summarize_attachment,
)

__all__ = [
    "AttachmentEntry",
    "SummaryRequest",
    "SummaryResult",
    "SummaryTier",
    "add_file",
    "append_attachments_section_to_files",
    "attachment_dir",
    "delete",
    "gather_entries",
    "get",
    "list_for_meeting",
    "parse_attachment_sidecar",
    "pick_tier",
    "pipeline_integration",
    "render_llm_context_block",
    "sidecar",
    "storage",
    "summarize_attachment",
    "summarizer",
    "wait_for_pending",
    "worker",
    "write_attachment_sidecar",
]
