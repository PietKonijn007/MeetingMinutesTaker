# Meeting Attachments — Files, Links, Screenshots

**Status:** Proposed.
**Scope tag:** Single-user, local-first MVP. No multi-user, no cloud, no auto-email.

## 1. Problem

Real meetings carry context that the audio doesn't capture: a slide deck the presenter is walking through, a PDF circulated the day before, a screenshot of a dashboard pasted into chat, a link to a spec. Today the pipeline only sees the transcript and (optionally) external app notes. The minutes either miss this context entirely or paraphrase it from whatever the speakers happened to say out loud — which is often wrong (numbers misheard, URLs garbled, slide titles paraphrased).

Three failure modes this leaves on the table:
- **Numbers and proper nouns drift.** Whisper transcribes "Q3 ARR was twelve point four million" as "12.4 million" when the slide actually said $12.7M. The minutes inherit the error.
- **Reader can't cross-check.** Readers who weren't in the meeting have no way to verify the summary against the source material — they have to ask the organizer for the deck.
- **Pre-prep context is lost.** A 30-page PDF circulated before a decision meeting is the entire reason the meeting exists. The minutes summarize what was said about it without ever reading it.

We need a way to attach files, links, and pasted images to a meeting; extract their text (OCR included); produce per-attachment summaries; inject those summaries into the minutes-generation prompt so the LLM has grounded context; and surface the attachments in the rendered minutes so a reader can click through to the source.

## 2. User-facing behavior

### 2.1 Attaching

Three entry points, all hitting the same backend:

1. **Pre-meeting** — on the Record page (and `/brief` page for an upcoming meeting), an "Attachments" panel with drag-and-drop zone and "Add link" inline form. Files attached here belong to the meeting that gets created when recording starts (the `meeting_id` is reserved up-front).
2. **During recording** — same panel stays visible on the Record page so the user can drag in a slide screenshot mid-meeting.
3. **Post-meeting** — new "Attachments" tab on the meeting detail page. Same controls, plus per-attachment delete and reprocess.

Supported inputs (MVP):
- Files: PDF, DOCX, PPTX, PNG, JPG, JPEG, HEIC. Max size from config (default 50 MB).
- Links: any HTTP(S) URL. We fetch and extract readable text via `trafilatura`.
- Pasted images: clipboard paste handler on all three pages, treated as a PNG upload.

Each attachment has user-editable fields: `title` (defaults to filename or page `<title>`) and `caption` (free text — "what this is and why it's attached"). The caption matters: it's a hint to the LLM about why this material was relevant.

### 2.2 Status flow

Per-attachment status badge in the UI:
- **uploading** — file transfer in flight.
- **extracting** — text-layer parsing or OCR running.
- **summarizing** — LLM summary call in flight.
- **ready** — sidecar markdown is complete, summary is in.
- **error** — extraction or summary failed; surface the error string with a Retry button.

Each new attachment kicks off the worker immediately on upload — independent of recording state. By the time recording stops and minutes generation runs, summaries for everything attached pre-recording are typically done. The pipeline waits up to `attachments.summary_wait_seconds` (default 60s) for any in-flight summaries before proceeding without them.

### 2.3 In the rendered minutes

After minutes generation, the pipeline post-appends a `## Attachments` section listing every ready attachment:

```markdown
## Attachments

### Q3 forecast deck
*Source: q3_forecast_v4.pptx — 18 slides — uploaded by user*

> Slide 1 covers the revenue summary: Q3 ARR landed at $12.7M, 4% above
> the September forecast, driven primarily by the EMEA mid-market
> segment. Slides 4-7 break down the EMEA wins by industry…
> [continues — variable length per attachment, see §4.3]

[View source](/api/attachments/abc-123/raw) · [Thumbnail](/api/attachments/abc-123/thumb)
```

The section is appended idempotently: any prior `## Attachments` block is stripped before the new one is written, same trick as `_strip_existing_section` in [external_notes.py:141](src/meeting_minutes/external_notes.py:141). This means regenerating the minutes never duplicates or loses the section.

The summary itself is also injected into the LLM minutes-generation prompt (§5.1), so it influences the body of the minutes — not just the appendix.

## 3. Data model

### 3.1 Database

New table via Alembic migration `007_attachments.py`:

```python
class AttachmentORM(Base):
    __tablename__ = "attachments"

    attachment_id     = Column(String, primary_key=True)        # uuid4
    meeting_id        = Column(String, ForeignKey("meetings.meeting_id",
                                                  ondelete="CASCADE"),
                              nullable=False, index=True)
    kind              = Column(String, nullable=False)          # 'file' | 'link' | 'image'
    source            = Column(String, nullable=False)          # 'upload' | 'paste' | 'preprep'
    original_filename = Column(String, nullable=True)
    mime_type         = Column(String, nullable=True)
    size_bytes        = Column(Integer, nullable=True)
    sha256            = Column(String, nullable=True)
    url               = Column(String, nullable=True)           # for kind='link'
    title             = Column(String, nullable=False)
    caption           = Column(Text, nullable=True)
    status            = Column(String, nullable=False, default="pending")
    error             = Column(Text, nullable=True)
    created_at        = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at        = Column(DateTime, nullable=False, default=datetime.utcnow,
                              onupdate=datetime.utcnow)
```

DB stores **only** the lightweight metadata. The extracted text and the LLM summary live in a sidecar markdown file (§3.2). Rationale: the markdown is the canonical context fed into minutes generation, easy to grep, easy to hand-edit, survives DB rebuilds, and matches the existing `data/external_notes/{id}.md` pattern.

Index on `meeting_id` for the common "list attachments for this meeting" query. `sha256` is computed for dedupe within a meeting (we silently ignore re-uploads of an identical file).

### 3.2 Filesystem layout

One directory per meeting under `data/attachments/`:

```
data/attachments/{meeting_id}/
  {attachment_id}{ext}            -- original bytes (e.g. .pdf, .pptx, .png)
  {attachment_id}.md              -- sidecar: extracted text + LLM summary
  {attachment_id}.thumb.jpg       -- thumbnail (PDF first page, image, or link og:image)
```

One folder per meeting keeps deletion and retention trivial — drop the folder when the meeting is deleted, same pattern the rest of `data/` uses.

The sidecar markdown structure:

```markdown
---
attachment_id: abc-123
meeting_id: m-456
kind: file
mime_type: application/pdf
title: Q3 forecast deck
source: q3_forecast_v4.pdf
extracted_at: 2026-05-01T14:32:11Z
extraction_method: pdf-text-layer  # | ocr | docx | pptx | link-trafilatura
summary_status: ready              # | pending | error
summary_target: medium             # | short | medium | long | xlong (see §4.3)
---

## Summary

<LLM-generated summary, variable length per §4.3>

## Extracted content

<full extracted text, verbatim — OCR output for images,
parsed text for PDF/DOCX/PPTX, readable extract for links>
```

The pipeline reads `## Summary` from this file when building the minutes prompt. The DB doesn't need to know about this structure — the API can serve the parsed sections by reading the file on demand.

## 4. Backend modules

### 4.1 Module layout

New package `src/meeting_minutes/attachments/`:

```
attachments/
  __init__.py        -- public API: add_file, add_link, delete, list_for_meeting, reprocess
  storage.py         -- DB row + filesystem operations
  extractors.py      -- per-kind text extraction (one function per kind)
  ocr.py             -- pytesseract wrapper, image preprocessing
  summarizer.py      -- LLM summary call with tiered prompts
  worker.py          -- async pipeline: extract → write sidecar → summarize → update sidecar
  sidecar.py         -- read/write the {attachment_id}.md frontmatter+sections
```

Modeled after the structure of [external_notes.py](src/meeting_minutes/external_notes.py) but split into multiple files because the surface area is bigger.

### 4.2 Extractors

One function per kind in `extractors.py`. All return `(extracted_text: str, extraction_method: str)`.

| Kind | Library | Notes |
|---|---|---|
| PDF (text layer) | `pypdf` (already a transitive dep via WeasyPrint) | Extract per-page text, join with `\n\n--- Page {n} ---\n\n` separators. |
| PDF (scanned) | `pypdf` + `pdf2image` + `pytesseract` | If text-layer extraction returns < 50 chars per page, fall back to rendering the page at 200 DPI and OCRing. |
| DOCX | `python-docx` (already a dep, used by EXP-1) | Walk paragraphs and tables in document order. |
| PPTX | `python-pptx` (new dep) | Per slide: title + body shapes + speaker notes. Format with `--- Slide {n}: {title} ---` separators. |
| Image (PNG/JPG/HEIC) | `pytesseract` + `Pillow` + `pillow-heif` | Always OCR. Auto-rotate, optional binarization for low-contrast screenshots. |
| Link | `httpx` + `trafilatura` | Fetch with timeout from config, follow redirects, extract main content. Capture `<title>` and og:image for thumbnail. |

OCR config: tesseract binary required on PATH. `install.sh` adds `brew install tesseract` next to ffmpeg in step [2.5/10]; `mm doctor` adds a `tesseract_installed` check.

Thumbnail generation in `storage.py`:
- PDF → first page rendered at 100 DPI via `pdf2image`.
- Image → resized copy max 400×400 via Pillow.
- DOCX/PPTX → no thumbnail in MVP (optional Phase 2).
- Link → og:image if available, else a generic globe icon SVG.

### 4.3 Summarizer

`summarizer.py` exposes one function:

```python
async def summarize_attachment(
    llm: LLMClient,
    title: str,
    caption: str | None,
    extraction_method: str,
    extracted_text: str,
) -> tuple[str, str]:
    """Returns (summary_markdown, target_tier)."""
```

Target tier picked by extracted-text length:

| Extracted text size | Tier | Target | Style |
|---|---|---|---|
| < 500 chars | `short` | 2–4 sentences | one paragraph, prose only |
| 500 – 5,000 chars | `medium` | 100–200 words | one paragraph, optional bullet list of key points |
| 5,000 – 30,000 chars | `long` | 250–500 words | sectioned with `###` subheaders matching the source structure (chapters, slide groups) |
| 30,000 – 100,000 chars | `xlong` | 500–800 words | sectioned + per-section bullet highlights, includes "Key numbers" and "Key entities" subheads |
| > 100,000 chars | `xlong` (chunked) | same as xlong | map-reduce: chunk to 30k-char windows, summarize each, then summarize-the-summaries to land within the xlong target |

Prompt is **separate** from the minutes-generation prompt — different system prompt, different schema, fired via its own `LLMClient.complete()` call. Inputs: tier-specific instructions, the title, the caption (so the LLM knows the user's framing), and the extracted text. Output: plain markdown (no JSON envelope) for direct embedding into the sidecar.

The summary should preserve verbatim quotes for numbers, dates, names, and direct quotes — the prompt explicitly instructs "do not paraphrase numbers; quote them exactly as they appear." This is the whole point of having grounded source material.

### 4.4 Worker

`worker.py` exposes:

```python
async def process_attachment(config: AppConfig, attachment_id: str) -> None:
    """End-to-end: extract → write sidecar → summarize → update sidecar.

    Runs as asyncio.create_task — must never raise. Records errors in the
    DB row's `status='error'` + `error` column and in the sidecar frontmatter.
    """
```

Lifecycle:
1. Load DB row, mark `status='extracting'`.
2. Dispatch to the right extractor by `kind` + `mime_type`. Write sidecar with the extracted content section and `summary_status: pending`.
3. Mark DB `status='summarizing'`.
4. Call `summarize_attachment`. Update sidecar with the `## Summary` section, set frontmatter `summary_status: ready` and `summary_target: <tier>`.
5. Mark DB `status='ready'`.

Fired from the API handler on every successful upload (and on `POST /reprocess`). Same pattern as [`schedule_background_update`](src/meeting_minutes/external_notes.py:363). Runs in the same event loop as the recording — recording is I/O-bound (audio capture + disk writes), so the LLM call won't starve it. If a local Ollama is the LLM and it shares a GPU with whisper, the summary may queue behind whisper after recording stops; that's fine because the summary doesn't need to land before transcription finishes — it needs to land before minutes generation starts.

## 5. Pipeline integration

### 5.1 Minutes generation

In [pipeline.py:614-664](src/meeting_minutes/pipeline.py:614), the existing code merges `user_notes` + `external_notes`. Add a third rail `attachment_context`:

```python
attachment_context = ""
attachment_dir = data_dir / "attachments" / meeting_id
if attachment_dir.exists():
    sidecars = sorted(attachment_dir.glob("*.md"))
    # Wait briefly for any in-flight summaries to complete.
    await _await_pending_attachments(
        sidecars,
        timeout_s=config.attachments.summary_wait_seconds,
    )
    blocks = []
    for sidecar in sidecars:
        meta, summary, _extracted = parse_attachment_sidecar(sidecar)
        if meta.get("summary_status") != "ready":
            _LOG.warning(
                "Attachment %s not ready after wait; skipping injection",
                meta.get("attachment_id"),
            )
            continue
        blocks.append(
            f"## ATTACHED MATERIAL: {meta['title']}\n"
            f"Source: {meta.get('source') or meta.get('url')}\n"
            f"Caption: {meta.get('caption', '')}\n\n"
            f"{summary}\n"
        )
    attachment_context = "\n\n".join(blocks)
```

The `attachment_context` is injected into the LLM system prompt under a clearly-labeled section (`## ATTACHED MATERIAL`) so the LLM treats it as ground-truth context, distinct from transcript content. The prompt instructions are updated to tell the LLM: "When the transcript references attached material, prefer the attached material's exact wording for numbers, names, and direct quotes."

We inject the **summaries**, not the full extracted text. Reasons: (a) extracted text can be huge (a 100-page PDF blows the context window); (b) the summary is a quality-controlled distillation already verified to preserve key facts; (c) injecting raw OCR output risks polluting the minutes with OCR garbage.

### 5.2 Post-append `## Attachments` section

After `MinutesJSONWriter` runs, before the markdown is finalized, post-append a `## Attachments` section listing every ready attachment with: title, source, summary, and a relative link to the raw + thumbnail endpoints.

Implementation lives in `attachments/__init__.py` as `append_attachments_section_to_local_files(data_dir, meeting_id)`, mirroring [`append_section_to_local_files`](src/meeting_minutes/external_notes.py:183). Idempotent strip-and-replace using `## Attachments` as the marker. Called from the same place in `pipeline.py` that currently re-appends external notes after regeneration.

### 5.3 Reprocess on attachment change

When an attachment is added, deleted, or its summary completes after minutes were already generated, the user can hit "Regenerate minutes with attachments" on the Attachments tab. This calls the existing `POST /meetings/{id}/regenerate` endpoint — no new pipeline plumbing. The next regen automatically picks up the current set of sidecars.

We deliberately do **not** auto-trigger regeneration on attachment add. The user might add five attachments back-to-back and we don't want to thrash the LLM. They click the button when they're done.

## 6. API surface

New router `src/meeting_minutes/api/routes/attachments.py`:

```
POST   /api/meetings/{id}/attachments              multipart upload (file)
       Body: file (multipart) + optional title, caption (form fields)
       → 202 { attachment_id, status: "extracting" }

POST   /api/meetings/{id}/attachments/link         JSON
       Body: { url, title?, caption? }
       → 202 { attachment_id, status: "extracting" }

GET    /api/meetings/{id}/attachments              list
       → [{ attachment_id, kind, title, caption, mime_type, size_bytes,
            status, error, created_at, has_thumbnail }]

GET    /api/attachments/{aid}                      detail
       → { ...metadata, summary, extracted_text }   (sidecar parsed on read)

GET    /api/attachments/{aid}/raw                  original bytes
       FileResponse with appropriate Content-Type and Content-Disposition

GET    /api/attachments/{aid}/thumb                thumbnail
       FileResponse, image/jpeg, 404 if no thumbnail

PATCH  /api/attachments/{aid}                      JSON
       Body: { title?, caption? }                  (mutable user fields only)
       → 200 { ...metadata }

DELETE /api/attachments/{aid}
       → 204                                       (removes DB row + folder contents)

POST   /api/attachments/{aid}/reprocess
       → 202 { status: "extracting" }              (re-runs the worker)
```

Validation at boundary (in the handlers):
- File size against `config.attachments.max_file_size_mb` — reject 413 if over.
- MIME type against `config.attachments.allowed_mime_types` — reject 415 if not in the list. (We trust the multipart Content-Type but also sniff the first 4096 bytes via `python-magic`.)
- URL must parse and have an `http://` or `https://` scheme — reject 422 otherwise.
- Reject any path traversal in returned filenames — `attachment_id` is uuid-only, never user-supplied.

## 7. Frontend

### 7.1 New components

```
web/src/lib/components/
  AttachmentsList.svelte       -- table/grid of attachments for a meeting
  AttachmentCard.svelte        -- single attachment row: thumb, title, status, actions
  AttachmentUploader.svelte    -- drag-drop zone + Add link form + paste handler
  AttachmentDetailModal.svelte -- click-through view: full summary + extracted text
```

`AttachmentsList` polls `GET /api/meetings/{id}/attachments` every 4s while any item is in `extracting`, `summarizing`, or `uploading`. Polling stops when all items are `ready` or `error`.

### 7.2 Page integration

- **`/record`** — `AttachmentUploader` and `AttachmentsList` in a new collapsible "Attachments" section beneath the recording controls. Visible before, during, and after recording. The `meeting_id` is reserved at the moment recording starts (or earlier if the user attaches first — we provision a meeting row in `pending` state).
- **`/brief`** — same uploader + list on the pre-meeting briefing page, scoped to whichever meeting the user is preparing for.
- **`/meeting/{id}`** — new "Attachments" tab in the existing tabset. Default to the Minutes tab; only switch the tab badge when an attachment is in error state.

### 7.3 Paste handler

A global `paste` event listener on the Record and Meeting Detail pages. When the user pastes an image (clipboard contains `image/*`), upload it as a PNG with `source='paste'` and a default title `"Pasted screenshot {timestamp}"`. The user can rename + caption it from the AttachmentCard.

## 8. Configuration

Add to `config/config.yaml`:

```yaml
attachments:
  enabled: true
  max_file_size_mb: 50
  allowed_mime_types:
    - application/pdf
    - application/vnd.openxmlformats-officedocument.wordprocessingml.document
    - application/vnd.openxmlformats-officedocument.presentationml.presentation
    - image/png
    - image/jpeg
    - image/heic
  links:
    fetch_timeout_seconds: 10
    user_agent: "MeetingMinutesTaker/1.0"
    follow_redirects: true
    max_response_mb: 10
  ocr:
    tesseract_binary: "tesseract"        # PATH lookup, override for non-standard installs
    languages: ["eng"]                   # add "nld" etc. as needed
    pdf_render_dpi: 200
  summary:
    chunk_threshold_chars: 100000        # above this, map-reduce summarize
    wait_for_pending_seconds: 60         # how long minutes generation waits for in-flight summaries
```

`AppConfig` (Pydantic) gets a corresponding `AttachmentsConfig` model in `config.py`.

## 9. Dependencies

New Python deps in `pyproject.toml`:

- `python-pptx` — PPTX parsing.
- `pytesseract` — OCR Python wrapper (the binary itself comes via `install.sh`).
- `pdf2image` — PDF page rendering for OCR fallback and thumbnails (depends on `poppler`, also installed via `install.sh`).
- `pillow-heif` — HEIC support (iPhone screenshots).
- `trafilatura` — readable-text extraction from web pages.
- `python-magic` — MIME sniffing (depends on `libmagic`, install.sh adds `brew install libmagic`).

`pypdf`, `python-docx`, `httpx`, `Pillow` are already direct or transitive deps.

`install.sh` additions in step [2.5/10]:
```bash
brew install tesseract poppler libmagic
```
`mm doctor` adds three checks: `tesseract_installed`, `poppler_installed`, `libmagic_installed`. Each surfaces install hints on failure.

## 10. Retention

`retention.py` already handles per-meeting cleanup. Add an `attachments_retention_days` config key (default `null` = keep forever) — when set, the daily retention sweep deletes attachments folders for meetings older than the threshold. Originals removed; sidecar markdown is kept (small, useful for the rendered minutes' `## Attachments` section, and the summary lives there). The "View source" link in rendered minutes 404s gracefully when the original is gone.

This is a deliberate split: the audio retention story is about disk space (audio is huge), and attachments are the same story. But unlike audio, the *summary* of an attachment is small enough to keep indefinitely, so we keep it.

## 11. Testing

Unit (Python):
- `extractors.extract_pdf` — text-layer PDF, scanned PDF (forces OCR fallback), encrypted PDF (graceful error).
- `extractors.extract_pptx` — slide titles + body + speaker notes survive in extracted text, in slide order.
- `extractors.extract_docx` — paragraphs + tables, in document order.
- `extractors.extract_image` — OCR happy path, rotated image, low-contrast screenshot.
- `extractors.extract_link` — valid HTML page, redirect chain, 404, timeout, non-HTML response.
- `summarizer.summarize_attachment` — each tier picks the right target, prompt includes title + caption, output is non-empty markdown.
- `summarizer` chunked path for >100k char inputs — verify map-reduce produces a single coherent summary within the xlong target.
- `worker.process_attachment` — happy path: status transitions, sidecar correctness. Failure paths: extraction raises, summarization raises, sidecar is partially written then resumed.
- `sidecar.parse_attachment_sidecar` — round-trip frontmatter + sections, tolerates missing sections.

Integration:
- Upload PDF → poll until ready → assert sidecar contains both extracted text and summary, DB row is `status='ready'`.
- Upload then trigger meeting regen → assert minutes markdown contains `## Attachments` section with entry, and the LLM-generated body shows evidence of grounding (numbers in the deck appear in the summary).
- Upload, delete, regen → assert `## Attachments` section reflects the deletion.
- Two uploads of identical file (same sha256) → second is ignored, no duplicate row.
- Concurrent regen + ongoing summarize → minutes generation waits up to `wait_for_pending_seconds` then proceeds (verify the timeout path doesn't deadlock).

Frontend:
- Visual regression on AttachmentCard for each status badge.
- Drag-drop accepts allowed types, rejects others with a toast.
- Paste handler fires on image paste, ignores text paste.

## 12. Out of scope

- **Per-attachment edit of the LLM-generated summary.** The user can re-trigger summarization (POST /reprocess) but cannot hand-edit the sidecar markdown via the UI. Anyone who wants to can edit the file on disk; that's the MVP single-user reality.
- **Attachment versioning.** New uploads with the same filename get a fresh `attachment_id`. No "revision history" within a single attachment.
- **In-line attachment references inside the minutes body.** The LLM is told the attachments exist and grounded on their summaries, but we don't generate `[1]`-style citations linking specific minutes paragraphs back to specific attachments. Possible Phase 2 if regen quality warrants it.
- **Attachment-aware speaker rename.** When a slide deck has presenter names on the title slide, we could feed those into the speaker-rename inference. Listed in Phase 2.
- **Search inside attachments.** Extracted text isn't indexed in FTS5 or embedded for chat. Possible Phase 2 — would expand the chat/search corpus meaningfully.
- **Attachments shared across meetings.** Each attachment belongs to exactly one meeting. If the same PDF is relevant to three meetings, it's uploaded three times. Single-user MVP scope.
- **OCR language autodetect.** Languages come from config. Multilingual OCR is a Phase 2 nice-to-have.

## 13. Migration / rollout

Migration `007_attachments.py` is purely additive — new table, no changes to existing tables.

If the feature is reverted: drop the table, drop `data/attachments/`, remove the config block. No other system depends on attachments existing. Existing meetings work unchanged because the pipeline's attachment loop is a no-op when `data/attachments/{meeting_id}/` doesn't exist.

`install.sh` additions for OCR/PDF deps (`tesseract`, `poppler`, `libmagic`) are skippable on existing installs — `mm doctor` will flag them as missing, and uploads requiring those deps will error with a clear "Install tesseract via `brew install tesseract`" message until installed. The web UI hides the upload zone entirely when `mm doctor` reports any of the three deps missing, with a banner pointing to the install hints.

## 14. Phasing

- **MVP (this spec):** file upload + link + paste, OCR (images and scanned PDFs), per-attachment LLM summary with tiered length, summary auto-injected into minutes generation, async worker fires on upload, `## Attachments` section appended post-generation, full pre-prep flow on Record and `/brief`.
- **Phase 2:** map-reduce summarization for >100k char attachments at production quality (MVP ships a working but unpolished version), attachment-aware speaker rename, FTS5 + embedding indexing of extracted text so attachments show up in search and chat, in-line citations from minutes body to attachments.
- **Phase 3:** multilingual OCR autodetect, video attachments (extract audio → transcribe via the existing whisper pipeline → treat the transcript as the extracted text).
