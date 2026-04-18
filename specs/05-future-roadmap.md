# Future Roadmap — Detailed Feature Specifications

This document contains detailed specifications for features not yet implemented. Each feature includes: description, technical approach, estimated effort, dependencies, and acceptance criteria.

---

## Recently Implemented

### Local AI Support (Implemented)

The following local AI features have been implemented:

- **Ollama LLM provider**: Full integration with Ollama's OpenAI-compatible API for local, free, offline meeting summarization. JSON-mode structured generation for meeting minutes output. Configurable base URL and timeout. Model discovery from local Ollama instance.
- **Transcription engine factory**: Pluggable transcription backends via `BaseTranscriptionEngine` ABC — Faster Whisper (CTranslate2, default) and Whisper.cpp (GGML quantized, optional via `pywhispercpp`).
- **Hardware detection**: Auto-detects GPU type (CUDA/Metal/CPU), VRAM, RAM and recommends appropriate Whisper and Ollama models. Available via `GET /api/config/hardware`.
- **Settings UI updates**: Transcription engine selector with install status badges, Ollama status indicator, hardware-aware model recommendations, dynamic Ollama model dropdown.

### Diarization & Speaker Experience (Implemented)

- **MPS acceleration for diarization**: `DiarizationEngine._load_pipeline()` auto-moves the pyannote pipeline to MPS (Apple Silicon), CUDA (NVIDIA), or CPU fallback. Delivers ~5-10× speedup on Apple Silicon.
- **Performance & Hardware settings**: UI toggle in Settings page for `PYTORCH_ENABLE_MPS_FALLBACK`. Config key: `performance.pytorch_mps_fallback`. Applied to process env vars via `AppConfig.model_post_init()` at startup.
- **Speaker name mapping**: `DiarizationEngine.apply_speaker_names()` maps `SPEAKER_XX` labels to user-provided names in first-speaking order. Wired into `run_transcription()` and `rediarize()` pipeline steps.
- **mm rediarize command**: Re-runs diarization only on existing audio without re-transcription; merges new speaker labels into the existing transcript JSON; chains into regeneration.
- **Speaker rename UI**: Inline editor on the Transcript tab ("✎ Name speakers") with "Save only" and "Save & regenerate minutes" actions. Backed by `PATCH /api/meetings/:id/transcript/speakers`.
- **pyannote.audio 3.3+ compat**: Handles both `Annotation` and `DiarizeOutput` return types. ffmpeg + torchcodec auto-installed by `install.sh`.
- **Diagnostic error messages**: `diarize.py` pattern-matches common error strings and surfaces actionable hints (install ffmpeg, accept HF license, install torchcodec).

### Minutes Display & Persistence (Implemented)

- **Structured card-based Minutes tab**: Replaces flat markdown render with Summary + Key topics + collapsible Discussion cards + Outcomes grid (Decisions/Actions) + Risks + Follow-ups + Parking lot.
- **structured_data persistence fix**: `MinutesJSONWriter` now populates `MinutesJSON.structured_data` so `structured_json` DB column gets written; resolves issue where discussion points etc. existed on disk but weren't returned by the API.
- **Sections fallback**: API always reads the on-disk minutes JSON to expose `sections[]` from the text+regex fallback path; UI renders them as collapsible cards when `discussion_points` is empty, with dedup against already-rendered sections.

### CLI & Install UX (Implemented)

- **Port conflict handling in `mm serve`**: Detects busy port via `lsof`, prompts to kill the holder, auto-pick the next free port, or abort. Auto-resolves non-interactively under launchd/systemd.
- **`mm upgrade` defaults to main branch**: No more surprise-stale-branch upgrades. `--branch` override for testing.
- **Hardware-aware whisper.cpp install**: `install.sh` detects platform and sets the right `WHISPER_*` env vars for source-build to enable Metal / CUDA / OpenBLAS.
- **ffmpeg auto-install**: New step [2.5/10] in `install.sh` installs ffmpeg via Homebrew.

---

## Security

### S2: API Authentication (JWT / API Key)

**Description**: Add authentication middleware to the FastAPI API so it's not open to anyone on the network. Support both API key (for programmatic access) and JWT token (for browser sessions).

**Technical Approach**:
- Add `security` section to `config.yaml`: `enabled: bool`, `api_key: str`, `jwt_secret: str`
- Create `src/meeting_minutes/api/auth.py` with FastAPI `Depends` middleware
- API key: check `X-API-Key` header or `?api_key=` query param
- JWT: issue token on login, verify on each request
- Exempt: `/api/health`, `/docs`, static files
- Web UI: store token in localStorage, attach to all requests

**Effort**: Medium (2-3 hours)

**Acceptance Criteria**:
- API returns 401 without valid credentials
- Web UI prompts for API key on first visit
- Config can disable auth for local-only use

---

### S3: PII Redaction

**Description**: Auto-detect and optionally redact personally identifiable information (names, emails, phone numbers, SSNs) from transcripts before storage.

**Technical Approach**:
- Use spaCy NER model (`en_core_web_sm`) for entity detection: PERSON, EMAIL, PHONE, ORG
- Add `privacy.pii_redaction: bool` to config
- Create `src/meeting_minutes/system2/redactor.py` with `redact_pii(text, entities_to_redact)` function
- Replace detected entities with `[REDACTED-PERSON]`, `[REDACTED-EMAIL]`, etc.
- Apply after transcription, before storage
- Store original (encrypted) separately if needed for review

**Effort**: Medium (3-4 hours)

**Dependencies**: spaCy, en_core_web_sm model

**Acceptance Criteria**:
- Names, emails, phone numbers detected and redacted
- Original text recoverable if configured
- Redaction toggle in Settings page

---

### S5: Audit Logging

**Description**: Track who accessed what transcript and when, stored in a separate audit table.

**Technical Approach**:
- Add `AuditLogORM` table: `id`, `timestamp`, `action` (view/edit/delete/export), `meeting_id`, `user` (from auth), `ip_address`
- FastAPI middleware logs each API request
- CLI: `mm audit log` to view recent activity
- Retention: configurable, default 90 days

**Effort**: Low-Medium (2 hours)

---

### S6: Consent Indicator

**Description**: Display a visible recording indicator and track consent per participant.

**Technical Approach**:
- System tray icon using `pystray` (macOS/Windows) showing recording status
- macOS notification when recording starts
- Store consent acknowledgment per meeting in TranscriptJSON metadata
- Option to require consent confirmation before recording starts

**Effort**: Medium (3 hours)

---

## Resilience & Robustness

### R5: Health Check Endpoint

**Description**: `GET /api/health` returning system status — DB connectivity, disk space, model availability, last backup time.

**Technical Approach**:
```python
@router.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "database": check_db_connectivity(),
        "disk_free_gb": get_disk_free(),
        "whisper_model_loaded": check_whisper_model(),
        "last_backup": get_last_backup_time(),
        "uptime_seconds": get_uptime(),
        "active_pipelines": len(_pipeline_jobs),
    }
```

**Effort**: Low (1 hour)

---

### R6: Graceful Model Download

**Description**: If Whisper model isn't downloaded, show a clear message in the UI with download progress instead of crashing.

**Technical Approach**:
- Check model existence at startup: `~/.cache/huggingface/hub/models--Systran--faster-whisper-{model}/`
- If missing, show a "Model Required" banner in the Record page with a "Download" button
- Download in background with progress reporting via WebSocket
- Block recording until model is available

**Effort**: Medium (2-3 hours)

---

### R8: Auto-Restart Service

**Description**: Run `mm serve` as a launchd service on macOS so it auto-starts on boot and restarts on crash.

**Technical Approach**:
- Create `~/Library/LaunchAgents/com.meetingminutes.server.plist`
- `mm install-service` CLI command to install/uninstall
- KeepAlive: true, RunAtLoad: true
- StandardOutPath/StandardErrorPath to logs directory

**Effort**: Low (1 hour)

---

## Improved Note-Taking

### N1: "Ask Your Meetings" — AI Chat Over Meeting History

**Description**: RAG-powered natural language search. Ask "What did we decide about the migration timeline?" and get answers with citations to specific meetings.

**Technical Approach**:
- Embedding store: ChromaDB or LanceDB (local, no server)
- Embed: meeting summaries, transcript segments, action items, decisions
- Model: `all-MiniLM-L6-v2` (local) or OpenAI `text-embedding-3-small`
- Query: embed user question → find similar chunks → send to LLM with context → return answer + citations
- UI: chat interface in a new "Ask" page or modal
- Re-embed on meeting create/update/delete

**Effort**: High (6-8 hours)

**Dependencies**: chromadb or lancedb, sentence-transformers

**Acceptance Criteria**:
- Natural language query returns relevant meeting excerpts
- Citations link to specific meetings
- Works offline with local embeddings

---

### N2: Voice Enrollment for Known Speakers

**Description**: Record 30-second voice samples of regular meeting attendees. Drops speaker identification error from ~15% to ~3%.

**Technical Approach**:
- UI: "People" page → click person → "Enroll Voice" → record 30 seconds
- Store voice embeddings in `voice-profiles/{person_id}.npy`
- During diarization, compare speaker embeddings against enrolled profiles
- pyannote supports speaker embedding extraction and comparison
- Map SPEAKER_XX labels to enrolled names automatically

**Effort**: High (4-6 hours)

**Dependencies**: pyannote.audio embedding model

---

### N3: Action Item Carry-Forward

**Description**: Unresolved action items from meeting N automatically appear in the context for meeting N+1 when the same attendees meet again.

**Technical Approach**:
- After transcription + attendee detection, query DB for open action items assigned to those attendees
- Inject into the LLM prompt: "PRIOR OPEN ACTION ITEMS: [list]"
- LLM generates a "Prior Action Items Review" section noting which were addressed
- Update action item status based on LLM analysis

**Effort**: Medium (3-4 hours)

---

### N4: Talk-Time Analytics

**Description**: Per-speaker talking percentage, question frequency, monologue detection.

**Technical Approach**:
- From diarized transcript segments: sum duration per speaker → percentages
- Question detection: regex for `?` at end of sentences, count per speaker
- Monologue: flag any uninterrupted speaking stretch > 3 minutes
- Store in MinutesJSON as `analytics` block
- Display in meeting detail page as a simple chart

**Effort**: Medium (3 hours)

---

### N5: Topic Tracker / Keyword Monitoring

**Description**: Set up alerts for when competitors, products, or strategic topics are mentioned across meetings.

**Technical Approach**:
- Config: `topics_to_track: ["competitor X", "migration", "budget"]`
- After transcription, scan full_text for keyword matches
- Store matches in a new `topic_mentions` table
- Dashboard: topic frequency over time, meetings where each topic appeared
- Optional: Slack webhook when a tracked topic is mentioned

**Effort**: Medium (3-4 hours)

---

### N8: Live Transcription Display

**Description**: Show real-time transcript in the web UI while recording using streaming Whisper.

**Technical Approach**:
- Use Distil-Whisper or `faster-whisper` in streaming mode
- Process audio in 5-10 second chunks during recording
- Push partial transcript via WebSocket to the browser
- Display scrolling text below the recording waveform
- Final transcription still uses full-quality batch mode after recording stops

**Effort**: High (6-8 hours)

**Dependencies**: faster-whisper streaming API

---

### N9: Meeting-Over-Meeting Comparison

**Description**: For recurring meetings, show what changed: new blockers, resolved items, mood trajectory.

**Technical Approach**:
- Detect recurring meetings by attendee overlap + similar title
- For each recurring series: track action items, blockers, mood across instances
- Generate a "Meeting Evolution" view showing trends
- LLM-powered diff: "What's new since last meeting?"

**Effort**: High (5-6 hours)

---

### N10: Semantic Search with Embeddings

**Description**: Search by meaning, not just keywords. "Meetings about scaling issues" finds discussions even without the word "scaling."

**Technical Approach**:
- Same embedding infrastructure as N1 (Ask Your Meetings)
- Add semantic search option to the existing search bar
- Hybrid: combine FTS5 keyword results with embedding similarity results
- Rank by combined score

**Effort**: Medium (3-4 hours, or included with N1)

---

### N12: Quality Thresholds Per Meeting Type

**Description**: Standups should have shorter summaries than decision meetings. Make quality checks type-aware.

**Technical Approach**:
- Add `quality_thresholds` to each template's YAML frontmatter or a separate config
- Default: standup (5-15%), team_meeting (15-25%), decision_meeting (20-35%)
- QualityChecker reads thresholds from template metadata

**Effort**: Low (1-2 hours)

---

## Integrations

### I1: Jira/Linear Ticket Creation

**Description**: One-click "Create Ticket" button on each action item, pre-filled from the minutes.

**Technical Approach**:
- Config: `integrations.jira.url`, `integrations.jira.api_token`, `integrations.jira.project_key`
- API endpoint: `POST /api/action-items/{id}/create-ticket`
- Pre-fill: summary from action item description, assignee from owner, due date
- Store ticket URL back on the action item record
- UI: small "Create Ticket" icon next to each action item

**Effort**: Medium (3-4 hours)

---

### I2: Slack Summary Posting

**Description**: After each meeting, post a summary + action items to a configured Slack channel.

**Technical Approach**:
- Config: `integrations.slack.webhook_url`, `integrations.slack.channel`
- After pipeline completion, POST formatted message to webhook
- Format: meeting title, summary, action items as bullet list, link to web UI
- Toggle per meeting type (e.g., always post team meetings, never post 1:1s)

**Effort**: Low (1-2 hours)

---

### I3: Email Minutes to Attendees

**Description**: Auto-send or one-click send the minutes to all participants.

**Technical Approach**:
- Config: SMTP server settings or Gmail API credentials
- `POST /api/meetings/{id}/email` endpoint
- HTML email with rendered minutes markdown
- Attach PDF version optionally
- CC manager optionally

**Effort**: Medium (3-4 hours)

---

### I4: Google Calendar Integration

**Description**: Auto-match recordings to calendar events, pull attendee names and meeting titles.

**Technical Approach**:
- Google Calendar API (OAuth2 flow)
- After recording starts, query calendar for events overlapping the recording time
- Extract: title, attendees, organizer, recurrence
- Pre-fill TranscriptJSON metadata
- Show calendar events in the Record page for one-click association

**Effort**: Medium (4-5 hours)

**Dependencies**: google-auth, google-api-python-client

---

### I5: Notion Page Export

**Description**: Push structured meeting notes to a Notion database.

**Technical Approach**:
- Notion API integration
- Config: `integrations.notion.api_key`, `integrations.notion.database_id`
- Create a page per meeting with properties matching YAML frontmatter
- Content as blocks (headings, bullet lists, checkboxes)

**Effort**: Medium (3-4 hours)

---

### I6: Webhook on Pipeline Completion

**Description**: Call a configurable URL when a meeting finishes processing.

**Technical Approach**:
- Config: `integrations.webhook.url`, `integrations.webhook.events: ["pipeline.complete", "pipeline.error"]`
- POST meeting metadata + summary to the URL after pipeline completes
- Include meeting_id, title, type, action_item_count, link

**Effort**: Low (1 hour)

---

## Analytics & Insights

### A1: Manager Dashboard

**Description**: Meetings per week, time in meetings, action item completion rates, most-met people, topic trends.

**Technical Approach**:
- Enhance existing Stats page with more charts
- Queries from existing DB data — no new storage needed
- Charts: meetings over time (already exists), action completion rate, people frequency, topic cloud

**Effort**: Medium (3-4 hours)

---

### A2: 1:1 Health Tracker

**Description**: For each direct report: mood trajectory over time, blocker resolution rate, career development progress, engagement signals.

**Technical Approach**:
- Extract mood/engagement from structured minutes (already in StructuredMinutesResponse)
- Store per-meeting per-person: mood_score, blocker_count, resolved_count
- People detail page: timeline chart of mood, blocker trend, career notes
- Alert when engagement signals turn negative for 2+ consecutive meetings

**Effort**: High (5-6 hours)

---

### A3: Decision Log with Search

**Description**: Cross-meeting decision database with rationale, searchable by topic.

**Technical Approach**:
- Already exists in the Decisions page
- Enhancement: add full-text search within decisions
- Add rationale display (already stored in DecisionORM)
- Add "Related Meetings" linking

**Effort**: Low (1-2 hours)

---

### A5: Customer Sentiment Tracking

**Description**: Across all customer meetings, track satisfaction trends per account.

**Technical Approach**:
- Extract sentiment from customer_meeting structured output
- New table: `customer_sentiment` with customer name, date, sentiment score, key concerns
- Dashboard: sentiment over time per customer, alert on declining trends

**Effort**: Medium (3-4 hours)

---

## Performance

### P3: Optimize Circular Audio Buffer

**Description**: Replace deque+concatenate with pre-allocated numpy arrays to avoid memory copies during long recordings.

**Technical Approach**:
- Pre-allocate a large numpy array (e.g., 1 hour at 48kHz = ~345MB)
- Write pointer tracks current position
- Read returns a view (no copy) of the filled portion
- Grows if recording exceeds pre-allocated size

**Effort**: Medium (2-3 hours)

---

### P4: Lazy Model Loading

**Description**: Don't load Whisper/pyannote models until the first recording starts.

**Technical Approach**:
- Already partially implemented (lazy imports)
- Enhancement: pre-warm models in a background thread after server startup
- Show "Model loading..." in Record page until ready

**Effort**: Low (1 hour)

---

### P5: Streaming Pipeline

**Description**: Start transcribing audio chunks while still recording.

**Technical Approach**:
- Split recording into 30-second segments
- Transcribe each segment as it completes
- Merge segment transcripts after recording stops
- Reduces total processing time by overlapping recording + transcription

**Effort**: High (6-8 hours)

---

## Testing

### T1-T4: Comprehensive Test Suite

**Description**: API integration tests, pipeline E2E tests, frontend tests, load tests.

**Effort**: High (8-10 hours total)

See individual items in the improvement roadmap.

---

## UI/UX

### U2: Bulk Operations

**Description**: Select multiple meetings for delete, re-process, or export.

**Technical Approach**:
- Checkbox on each meeting card in list view
- Floating action bar when items selected: Delete, Reprocess, Export
- Batch API endpoints: `POST /api/meetings/bulk-delete`, `POST /api/meetings/bulk-reprocess`

**Effort**: Medium (3-4 hours)

---

### U3: Keyboard Shortcuts

**Description**: Cmd+R to record, Cmd+N for new upload, Cmd+K for search (already works).

**Effort**: Low (1 hour)

---

### U4: Mobile Responsive

**Description**: Calendar view and meeting detail don't work well on phone screens.

**Technical Approach**:
- Stack panels vertically on mobile
- Bottom sheet for meeting detail
- Touch-friendly controls

**Effort**: Medium (3-4 hours)

---

### U5: Accessibility (ARIA Labels)

**Description**: Screen reader support for all interactive elements.

**Effort**: Medium (2-3 hours)

---

### U6: Offline PWA Mode

**Description**: Make the web UI work offline as a Progressive Web App.

**Technical Approach**:
- Service worker for caching static assets
- IndexedDB for offline meeting storage
- Sync when back online

**Effort**: High (6-8 hours)
