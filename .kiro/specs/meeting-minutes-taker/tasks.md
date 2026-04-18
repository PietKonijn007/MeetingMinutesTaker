# Implementation Plan: Meeting Minutes Taker

## Overview

Incremental implementation of the three-system meeting minutes pipeline. Each task builds on previous work, with property tests placed close to the code they validate. The implementation follows the system boundaries (System 1 → System 2 → System 3) and wires them together via the pipeline orchestrator at the end.

## Tasks

- [x] 1. Set up project structure, shared models, and configuration
  - [x] 1.1 Create pyproject.toml with dependencies (sounddevice, faster-whisper, pyannote.audio, anthropic, openai, sqlalchemy, typer, pyyaml, jinja2, hypothesis, pytest, watchdog, pydantic) and project metadata
    - _Requirements: 13.1_
  - [x] 1.2 Implement shared Pydantic data models in `src/meeting_minutes/models.py` — TranscriptJSON, MinutesJSON, enums (MeetingType, ActionItemStatus, ReviewStatus), and all nested models (TranscriptSegment, WordTimestamp, SpeakerMapping, ActionItem, Decision, MinutesSection, etc.)
    - _Requirements: 4.1, 4.2, 7.1, 7.2_
  - [x]* 1.3 Write property tests for TranscriptJSON and MinutesJSON round-trip serialization
    - **Property 6: Transcript JSON round-trip**
    - **Property 15: Minutes JSON round-trip**
    - **Validates: Requirements 4.4, 7.4**
  - [x] 1.4 Implement ConfigLoader in `src/meeting_minutes/config.py` — load YAML, apply defaults, validate required fields, return AppConfig Pydantic model
    - _Requirements: 13.1, 13.2, 13.3, 13.4_
  - [x]* 1.5 Write property tests for configuration loading
    - **Property 32: Configuration loading round-trip**
    - **Property 33: Invalid configuration rejection**
    - **Validates: Requirements 13.1, 13.2, 13.4**
  - [x] 1.6 Implement StructuredLogger in `src/meeting_minutes/logging.py` — JSON-formatted log entries with timestamp, log_level, system_name, meeting_id correlation, configurable log levels
    - _Requirements: 14.1, 14.2, 14.3, 14.4_
  - [x]* 1.7 Write property tests for structured logging
    - **Property 34: Structured log format**
    - **Property 35: Log correlation ID**
    - **Property 36: Log level filtering**
    - **Validates: Requirements 14.1, 14.2, 14.3**

- [x] 2. Checkpoint — Verify shared foundation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Implement System 1: Audio Capture and Transcription
  - [x] 3.1 Implement AudioCaptureEngine in `src/meeting_minutes/system1/capture.py` — start/stop recording, circular buffer, UUID meeting_id generation, FLAC output, silence-based auto-stop, metadata collection
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_
  - [x]* 3.2 Write property tests for audio capture components
    - **Property 1: Circular buffer retains most recent samples**
    - **Property 2: Meeting ID uniqueness**
    - **Property 3: Recording metadata completeness**
    - **Validates: Requirements 1.3, 1.5, 1.6**
  - [x] 3.3 Implement TranscriptionEngine in `src/meeting_minutes/system1/transcribe.py` — faster-whisper integration, configurable model size, word-level timestamps, confidence scores, language detection, custom vocabulary support, fallback engine logic
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7_
  - [x] 3.4 Implement DiarizationEngine in `src/meeting_minutes/system1/diarize.py` — pyannote.audio integration, speaker label assignment (SPEAKER_XX pattern), num_speakers counting, graceful failure handling
    - _Requirements: 3.1, 3.2, 3.3, 3.4_
  - [x] 3.5 Implement TranscriptJSONWriter in `src/meeting_minutes/system1/output.py` — combine transcription + diarization + metadata into Transcript_JSON, write to configured directory with UTF-8 encoding
    - _Requirements: 4.1, 4.2, 4.3, 4.4_
  - [x]* 3.6 Write property tests for transcription and diarization output
    - **Property 4: Transcription output completeness**
    - **Property 5: Transcript JSON schema validity**
    - **Property 7: Diarization output consistency**
    - **Validates: Requirements 2.3, 2.7, 3.2, 3.3, 4.1, 4.2**

- [x] 4. Checkpoint — Verify System 1
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement System 2: Minutes Generation
  - [x] 5.1 Implement TranscriptIngester in `src/meeting_minutes/system2/ingest.py` — load Transcript_JSON from file, validate against schema, pre-process (replace speaker labels with names, merge short segments)
    - _Requirements: 5.1, 5.2_
  - [x]* 5.2 Write property tests for transcript ingestion
    - **Property 8: Transcript schema validation**
    - **Property 9: Speaker label replacement**
    - **Validates: Requirements 5.1, 5.2**
  - [x] 5.3 Implement PromptRouter in `src/meeting_minutes/system2/router.py` — template selection based on meeting type + confidence, user override support, fallback to "other" template
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_
  - [x]* 5.4 Write property test for prompt router selection logic
    - **Property 13: Prompt router selection logic**
    - **Validates: Requirements 6.1, 6.3, 6.4**
  - [x] 5.5 Implement PromptTemplateEngine in `src/meeting_minutes/system2/prompts.py` — Jinja2 template rendering, create default templates (general, standup, decision_meeting, one_on_one, customer_meeting, brainstorm, retrospective, planning) in `templates/` directory
    - _Requirements: 5.3, 6.5_
  - [x]* 5.6 Write property test for prompt construction
    - **Property 10: Prompt construction completeness**
    - **Validates: Requirements 5.3**
  - [x] 5.7 Implement LLMClient in `src/meeting_minutes/system2/llm_client.py` — async Anthropic/OpenAI/OpenRouter/Ollama API calls, retry with exponential backoff, fallback provider, token usage tracking, cost calculation
    - _Requirements: 5.6, 5.7, 5.8, 5.9_
  - [x] 5.8 Implement MinutesParser in `src/meeting_minutes/system2/parser.py` — parse LLM markdown response into ParsedMinutes (summary, sections, action_items, decisions, key_topics)
    - _Requirements: 5.4_
  - [x]* 5.9 Write property test for minutes parser
    - **Property 11: Minutes parser extraction**
    - **Validates: Requirements 5.4**
  - [x] 5.10 Implement QualityChecker in `src/meeting_minutes/system2/quality.py` — speaker coverage check, length ratio check (10-30%), hallucination detection (names/dates/numbers not in transcript)
    - _Requirements: 8.1, 8.2, 8.3, 8.4_
  - [x]* 5.11 Write property tests for quality checker
    - **Property 16: Speaker coverage in minutes**
    - **Property 17: Minutes length ratio**
    - **Property 18: Hallucination detection**
    - **Validates: Requirements 8.1, 8.2, 8.3**
  - [x] 5.12 Implement MinutesJSONWriter in `src/meeting_minutes/system2/output.py` — serialize ParsedMinutes + QualityReport + LLM metadata to Minutes_JSON and Markdown files
    - _Requirements: 5.5, 7.1, 7.2, 7.3_
  - [x]* 5.13 Write property test for minutes output
    - **Property 14: Minutes JSON schema validity**
    - **Property 12: Minutes output file creation**
    - **Validates: Requirements 5.5, 7.1, 7.2**

- [x] 6. Checkpoint — Verify System 2
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement System 3: Storage and Search
  - [x] 7.1 Implement SQLAlchemy ORM models in `src/meeting_minutes/system3/db.py` — MeetingORM, TranscriptORM, MinutesORM, ActionItemORM, DecisionORM, PersonORM, meeting_attendees association table, FTS5 virtual table creation, database session factory, Alembic initial migration
    - _Requirements: 9.1_
  - [x] 7.2 Implement StorageEngine in `src/meeting_minutes/system3/storage.py` — upsert_meeting (with person entity extraction), get_meeting, list_meetings, delete_meeting (DB + filesystem + FTS index), upsert_person, get/update action items
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 15.1, 15.2, 15.3, 15.4_
  - [x]* 7.3 Write property tests for storage engine
    - **Property 19: Meeting storage round-trip**
    - **Property 20: Attendee person entity creation**
    - **Property 21: Storage upsert idempotence**
    - **Property 22: FTS index sync after storage**
    - **Property 37: Complete meeting deletion**
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 15.1, 15.2, 15.3**
  - [x] 7.4 Implement SearchEngine in `src/meeting_minutes/system3/search.py` — FTS5 query execution, query parsing (extract type:, after:, before: filters from raw query), BM25 ranking, phrase matching, boolean operators, reindex/remove methods
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_
  - [x]* 7.5 Write property tests for search engine
    - **Property 23: FTS phrase matching**
    - **Property 24: FTS boolean operators**
    - **Property 25: Date range filter**
    - **Property 26: Meeting type filter**
    - **Property 27: BM25 ranking order**
    - **Validates: Requirements 10.2, 10.3, 10.4, 10.5, 10.6**
  - [x] 7.6 Implement MinutesIngester in `src/meeting_minutes/system3/ingest.py` — parse Minutes_JSON, call StorageEngine.upsert_meeting, call SearchEngine.reindex_meeting
    - _Requirements: 9.1, 9.4_

- [x] 8. Checkpoint — Verify System 3
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement CLI Interface
  - [x] 9.1 Implement CLI commands in `src/meeting_minutes/system3/cli.py` using typer — `mm search`, `mm list`, `mm show`, `mm actions`, `mm actions complete`, `mm delete`, `mm record start/stop`, `mm generate`, `mm reprocess`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_
  - [x]* 9.2 Write property tests for CLI
    - **Property 28: CLI list chronological order**
    - **Property 29: CLI action items filtering**
    - **Property 30: CLI invalid ID error handling**
    - **Validates: Requirements 11.3, 11.5, 11.6, 11.9**
  - [x]* 9.3 Write unit tests for CLI commands
    - Test `mm search` with various filter combinations
    - Test `mm show` with valid and invalid meeting IDs
    - Test `mm actions complete` updates status
    - Test `mm delete` removes all associated data
    - _Requirements: 11.1, 11.2, 11.4, 11.7, 11.8_

- [x] 10. Checkpoint — Verify CLI
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement Pipeline Orchestrator and wire systems together
  - [x] 11.1 Implement PipelineOrchestrator in `src/meeting_minutes/pipeline.py` — coordinate System 1 → 2 → 3, support automatic/semi_automatic/manual modes, filesystem watcher (watchdog) for automatic mode, reprocess command
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_
  - [x]* 11.2 Write property test for reprocess idempotence
    - **Property 31: Reprocess idempotence**
    - **Validates: Requirements 12.5**
  - [x]* 11.3 Write unit tests for pipeline orchestration
    - Test automatic mode triggers System 2 after System 1 completes
    - Test semi_automatic mode waits for manual trigger
    - Test manual mode requires separate triggers
    - Test pipeline error handling (System 1 failure does not trigger System 2)
    - _Requirements: 12.1, 12.2, 12.3, 12.4_
  - [x] 11.4 Wire CLI entry point in `src/meeting_minutes/__init__.py` or `__main__.py` — connect CLI commands to PipelineOrchestrator, StorageEngine, SearchEngine with proper config loading and dependency injection
    - _Requirements: 11.1, 12.1, 13.1_

- [x] 12. Create default prompt templates
  - [x] 12.1 Create Jinja2 prompt templates in `templates/` — general.md.j2, standup.md.j2, decision_meeting.md.j2, one_on_one.md.j2, customer_meeting.md.j2, brainstorm.md.j2, retrospective.md.j2, planning.md.j2, each with type-specific extraction instructions and output format
    - _Requirements: 6.5_

- [x] 13. Create default configuration and Alembic setup
  - [x] 13.1 Create default `config/config.yaml` with all documented defaults
    - _Requirements: 13.3_
  - [x] 13.2 Set up Alembic for database migrations — alembic.ini, initial migration script creating all tables and FTS5 virtual table
    - _Requirements: 9.1_

- [x] 14. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Structured JSON Output implementation
  - [x] 15.1 Implement StructuredMinutesResponse Pydantic model in `src/meeting_minutes/system2/schema.py` with all fields (summary, sentiment, participants, discussion_points, action_items, decisions, risks_and_concerns, follow_ups, parking_lot, meeting_effectiveness, key_topics, structured_data, minutes_markdown)
    - _Requirements: 16.1, 18.1_
  - [x] 15.2 Implement `LLMClient.generate_structured()` method with tool_use and forced tool_choice
    - _Requirements: 16.1, 16.2_
  - [x] 15.3 Implement StructuredMinutesAdapter to convert StructuredMinutesResponse to ParsedMinutes
    - _Requirements: 16.3_
  - [x] 15.4 Implement fallback from structured to text+regex when tool_use fails
    - _Requirements: 17.1, 17.2_
  - [x] 15.5 Update MinutesJSON, ActionItem, Decision models with new fields (priority, rationale, confidence, transcript_segment_ids)
    - _Requirements: 18.1_
  - [x]* 15.6 Write property tests for structured output
    - **Property 38: Structured output schema validity**
    - **Property 39: Structured output adapter completeness**
    - **Property 40: Structured output fallback**
    - **Validates: Requirements 16.1, 16.2, 16.3, 17.1**

- [x] 16. REST API + Web UI implementation
  - [x] 16.1 Implement FastAPI app factory in `src/meeting_minutes/api/main.py` with CORS, static file serving, and OpenAPI docs
    - _Requirements: 20.1, 20.3, 20.4_
  - [x] 16.2 Implement dependency injection in `src/meeting_minutes/api/deps.py`
    - _Requirements: 20.1_
  - [x] 16.3 Implement Pydantic response schemas in `src/meeting_minutes/api/schemas.py`
    - _Requirements: 20.2_
  - [x] 16.4 Implement all route modules (meetings, search, actions, decisions, people, stats, recording, config) — 32 endpoints total
    - _Requirements: 20.2_
  - [x] 16.5 Implement WebSocket handlers for recording status and pipeline progress
    - _Requirements: 20.2_
  - [x] 16.6 Add `mm serve` and `mm init` CLI commands
    - _Requirements: 20.1_
  - [x] 16.7 Build Svelte frontend with SvelteKit + Tailwind CSS (8 pages, 14 components)

- [x] 17. Environment and configuration improvements
  - [x] 17.1 Implement `env.py` for .env file loading with priority over environment variables
    - _Requirements: 19.1, 19.2_
  - [x] 17.2 Add Alembic migration 002_structured_minutes for new database columns (sentiment, structured_json, priority, rationale, confidence)
    - _Requirements: 18.2_

- [x] 18. LLM-based meeting type classifier
  - [x] 18.1 Replace keyword-matching classifier with LLM call using Claude Haiku in `src/meeting_minutes/system2/router.py` — uses Anthropic tool_use with enum constraint, sends first 4000 chars of transcript + metadata (speaker count, calendar title), returns meeting_type/confidence/reasoning
    - _Requirements: 21.1, 21.2_
  - [x] 18.2 Implement `_extract_type_descriptions()` to read template files (system prompt + section headings) for template-aware classification, auto-discovers custom types
    - _Requirements: 21.3, 21.4_
  - [x] 18.3 Add fallback to keyword matching when Anthropic API is unavailable
    - _Requirements: 21.5_

- [x] 19. Team meeting template
  - [x] 19.1 Add `TEAM_MEETING` to MeetingType enum in `src/meeting_minutes/models.py`
  - [x] 19.2 Create `templates/team_meeting.md.j2` with sections: Prior Action Items Review, Decisions (with rationale), Financial Review (cloud spend, optimization, P&L), Blockers (4 categories + cross-team dependencies), Strategic Updates, Technology Decisions, Service Feedback (AWS/NetApp), Customer Impact, Resource & Capacity, Team Health & Morale, Announcements, Parking Lot, Action Items (split by urgency)
  - [x] 19.3 Add `team_meeting` to TEMPLATE_FILENAME_MAP in router

- [x] 20. Enhanced 1:1 template
  - [x] 20.1 Rewrite `templates/one_on_one.md.j2` with enriched sections: Mood/Energy Check-in, Accomplishments & Wins (dated), Progress Against Objectives, Blockers (4 types), Feedback Given (SBI format), Feedback Received, Service Feedback, Customer Feedback, Team & Org Observations, Career Development, Coaching Notes, Engagement Signals, Action Items (split employee vs manager)

- [x] 21. Auto-detect capture device
  - [x] 21.1 Implement `auto_select_capture_device()` function in `src/meeting_minutes/system1/capture.py` — prefers MeetingCapture aggregate devices, tests each device by opening a brief stream, skips offline devices
    - _Requirements: 22.1, 22.2_
  - [x] 21.2 Add API endpoint `GET /api/auto-detect-device`
    - _Requirements: 22.3_
  - [x] 21.3 Update Record page to auto-select best device on load with "auto-detected" indicator
    - _Requirements: 22.4_

- [x] 22. Audio capture improvements
  - [x] 22.1 Add device fallback: retry with default device on macOS audio errors
  - [x] 22.2 Add explicit blocksize=1024 for predictable callback timing
  - [x] 22.3 Implement auto-save every 5 minutes during recording (recovery file)
    - _Requirements: 23.1, 23.2_
  - [x] 22.4 Add multi-channel capture: open all channels on aggregate devices, mix to mono

- [x] 23. Concurrent pipeline and threading fixes
  - [x] 23.1 Separate recording and pipeline state fully — can record, stop, immediately record again
  - [x] 23.2 Queue pipeline jobs for sequential processing (prevents memory thrashing)
  - [x] 23.3 Track each job with step/progress/error, WebSocket push for real-time status (replaced HTTP polling)
  - [x] 23.4 Auto-cleanup after 60 seconds
  - [x] 23.5 Add _frames_lock protecting audio buffer from concurrent access
  - [x] 23.6 Use stream.stop() instead of abort() for safe shutdown
  - [x] 23.7 PortAudio re-scan only when not recording
  - [x] 23.8 Graceful shutdown on Ctrl+C

- [x] 24. Batch 2 — Phase 1: Pipeline robustness
  - [x] 24.1 Implement `_retry_async` in PipelineOrchestrator with exponential backoff (up to 2 retries, 5s base delay)
  - [x] 24.2 Implement encryption at rest (`src/meeting_minutes/encryption.py`) using Fernet symmetric encryption
  - [x] 24.3 Implement retention policy engine (`src/meeting_minutes/retention.py`) with configurable per-type retention periods
  - [x] 24.4 Add SecurityConfig, RetentionConfig, APIConfig to config models
  - [x] 24.5 Add security route (`src/meeting_minutes/api/routes/security.py`) with key generation endpoint
  - [x] 24.6 Add retention route (`src/meeting_minutes/api/routes/retention.py`) with status and cleanup endpoints

- [x] 25. Batch 2 — Phase 2: Transcription improvements
  - [x] 25.1 Add Distil-Whisper model support (distil-medium.en, distil-large-v3)
  - [x] 25.2 Add Metal acceleration detection and fallback for Apple Silicon

- [x] 26. Batch 2 — Phase 3: Live note-taking during recording
  - [x] 26.1 Add speaker names, notes, and custom LLM instructions fields to Record page
  - [x] 26.2 Save notes to `data/notes/{meeting_id}.json` during recording
  - [x] 26.3 Load notes during pipeline processing and include in LLM prompt context

- [x] 27. Batch 2 — Phase 4: Analytics enrichment
  - [x] 27.1 Add per-speaker sentiment to ParticipantInfo in StructuredMinutesResponse
  - [x] 27.2 Add meeting effectiveness score (1-5 rating) to generated minutes
  - [x] 27.3 Add meeting type refinement (N11) for borderline classification cases

- [x] 28. Batch 2 — Phase 5: Search with filters in calendar view
  - [x] 28.1 Add search bar above calendar in the left panel of the Meetings page
  - [x] 28.2 Implement debounced search (300ms) calling the search API
  - [x] 28.3 Show search results with title, date, type badge, and snippet
  - [x] 28.4 Click search result loads meeting detail in right panel
  - [x] 28.5 Clear search returns to normal calendar/day list view

- [x] 29. Batch 2 — Phase 6: Documentation updates
  - [x] 29.1 Update README.md with all batch 2 features
  - [x] 29.2 Update docs/USER_GUIDE.md with encryption, retention, live note-taking, search
  - [x] 29.3 Update specs/00-architecture.md with encryption, retention, pipeline retry, data/notes/
  - [x] 29.4 Update specs/01-recording-and-transcription.md with Distil-Whisper, Metal, live notes
  - [x] 29.5 Update specs/02-minutes-generation.md with custom instructions, sentiment, effectiveness
  - [x] 29.6 Update .kiro/specs design.md with new files and config models
  - [x] 29.7 Update .kiro/specs tasks.md with batch 2 tasks

- [x] 31. Diarization reliability & speaker experience
  - [x] 31.1 Install ffmpeg + torchcodec as hard deps (pyannote.audio 3.3+ requirement). install.sh step 2.5/10 `brew install ffmpeg`; pyproject adds `torchcodec>=0.1.0`.
  - [x] 31.2 Support pyannote 3.3+ DiarizeOutput wrapper in `DiarizationEngine.diarize()` (unwrap via `speaker_diarization` / `diarization` / `annotation` attrs).
  - [x] 31.3 Auto-move pyannote pipeline to best available device (MPS/CUDA/CPU) in `DiarizationEngine._load_pipeline()` for 5-10× speedup.
  - [x] 31.4 Actionable diagnostics in `DiarizationEngine.diarize()` exception handler — pattern-match common error strings and suggest fixes.
  - [x] 31.5 Add `mm rediarize <meeting_id>` CLI command + `PipelineOrchestrator.rediarize()` method — re-run diarization on existing audio without re-transcribing; optionally chain into regeneration.
  - [x] 31.6 `DiarizationEngine.apply_speaker_names()` — map SPEAKER_XX labels to user-provided names in first-speaking order; wired into `run_transcription()` and `rediarize()`.
  - [x] 31.7 Inline speaker rename UI on Transcript tab ("✎ Name speakers" button) backed by `PATCH /api/meetings/:id/transcript/speakers` endpoint.
  - [x] 31.8 PerformanceConfig in config + Settings UI toggle for `PYTORCH_ENABLE_MPS_FALLBACK`. Applied at startup via `AppConfig.model_post_init()`.
  - [x] 31.9 Surface diarization events in server.log — success, failure, disabled, empty segments. Fixes silent-failure mode where diarization returned 0 segments with no log entry.

- [x] 32. Minutes display & persistence
  - [x] 32.1 Card-based Minutes tab — collapsible discussion topics, outcomes grid, risks, follow-ups, parking lot cards with "Raw markdown" fallback toggle. Replaces flat markdown render.
  - [x] 32.2 Fix structured data persistence — `MinutesJSONWriter` now populates `MinutesJSON.structured_data` so `StorageEngine.upsert_meeting()` writes it to `minutes.structured_json` DB column. Resolves issue where discussion_points existed on disk but API returned empty arrays.
  - [x] 32.3 Sections fallback rendering — API always reads on-disk minutes JSON to extract `sections[]` (never stored in DB); UI renders sections as collapsible cards when `discussion_points` is empty. Deduplicates headings already rendered as dedicated cards.
  - [x] 32.4 Expose `discussion_points`, `risks_and_concerns`, `follow_ups`, `parking_lot`, `key_topics`, `sections`, `sentiment` in `MinutesResponse` schema.
  - [x] 32.5 Transcript segments + speakers exposed via `GET /api/meetings/:id/transcript` — read from on-disk JSON since segments aren't in DB.
  - [x] 32.6 Color-coded per-speaker rendering in Transcript tab with legend bar.

- [x] 33. CLI & install UX
  - [x] 33.1 `mm serve` port conflict handling — detect via `lsof`, prompt kill/next/abort; auto-resolve non-interactively under launchd/systemd. `--auto-port/--no-auto-port` flag.
  - [x] 33.2 `mm upgrade` always pulls from main by default (`--branch` override); auto-switches branch if not on main. Prevents stuck-on-feature-branch upgrade surprises.
  - [x] 33.3 `mm upgrade` step [3b/5] auto-installs pywhispercpp with hardware-specific build flags if missing.
  - [x] 33.4 `install.sh` detects platform (Apple Silicon/Intel Mac/Linux+CUDA/Linux+ROCm/Linux CPU) and sets `WHISPER_METAL=1` / `WHISPER_CUDA=1` / `WHISPER_HIPBLAS=1` / `WHISPER_OPENBLAS=1` for source build of pywhispercpp.
  - [x] 33.5 `install.sh` step [2.5/10] installs ffmpeg via Homebrew (required by pyannote 3.3+/torchcodec).

- [x] 35. People management (edit / delete / merge)
  - [x] 35.1 Add `PATCH /api/people/:id` — rename/change email. On rename, cascade to `action_items.owner` and `decisions.made_by`. Reject with 409 on email conflict.
  - [x] 35.2 Add `DELETE /api/people/:id` — remove from `meeting_attendees`; preserve historical `owner`/`made_by` strings.
  - [x] 35.3 Add `POST /api/people/:id/merge` — reassign meeting_attendees (deduplicated), optionally rewrite historical attributions, carry over source email, delete source.
  - [x] 35.4 Person detail page: inline Edit form (name + email), Merge modal (target dropdown sorted by name with meeting counts + "rename actions" checkbox), Delete ConfirmModal.
  - [x] 35.5 Fix People list page id-field mismatch (person_id vs id) so the list actually renders; add visible error state.
  - [x] 35.6 Resolve `sqlite_path` relative to project root (not CWD) so CLI commands from any directory hit the same DB as launchd-spawned mm serve. Added `resolve_db_path()` helper used by all 11 call sites.
  - [x] 35.7 Add `backups/` to .gitignore so `mm upgrade` doesn't warn about untracked directory.

- [x] 34. Semantic search & chat ("Talk to your meetings")
  - [x] 34.1 Add `sentence-transformers` and `sqlite-vec` to dependencies
  - [x] 34.2 Add `EmbeddingChunkORM`, `ChatSessionORM`, `ChatMessageORM` to db.py; create `embedding_vectors` sqlite-vec virtual table
  - [x] 34.3 Build `EmbeddingEngine` in `embeddings.py` — chunk meetings (transcript sliding window + structured fields), embed with bge-small, store in sqlite-vec
  - [x] 34.4 Build `ChatEngine` in `chat.py` — query filter parsing (person/date/topic), semantic search, context building, LLM RAG synthesis, conversation history
  - [x] 34.5 Add auto-embed step to pipeline (after ingestion) + `_embed_meeting()` method
  - [x] 34.6 Add `mm embed` CLI command with progress bar, `--force` flag, single/all modes
  - [x] 34.7 Add chat API routes (`POST /api/chat`, `GET /api/chat/sessions`, `GET /api/chat/sessions/:id/messages`, `DELETE /api/chat/sessions/:id`)
  - [x] 34.8 Build Chat page frontend with conversation sidebar, suggested queries, markdown answers with citations, auto-scroll, session management
  - [x] 34.9 Add "Chat" to sidebar navigation with icon
  - [x] 34.10 Fix duplicate search bar: rename in-calendar search placeholder to "Filter meetings..."

- [x] 30. Local AI Support
  - [x] 30.1 Implement Ollama LLM provider in `src/meeting_minutes/system2/llm_client.py` — `_call_ollama()` via OpenAI-compatible API, no API key required, configurable base_url, $0.00 cost tracking
    - _Requirements: 5.8, 5.9_
  - [x] 30.2 Implement JSON-mode structured generation for non-Anthropic providers — `_generate_structured_via_json()` embeds schema in system prompt, strips markdown fences, parses JSON
    - _Requirements: 5.9_
  - [x] 30.3 Add Ollama model fetcher in `src/meeting_minutes/api/model_fetcher.py` — queries local Ollama `/api/tags` for pulled models with size/family/quantization info
  - [x] 30.4 Create hardware detection module in `src/meeting_minutes/hardware.py` — detects GPU type (CUDA/Metal/CPU), VRAM, RAM, recommends Whisper + Ollama models, checks Ollama install/running status
  - [x] 30.5 Refactor transcription engine to factory pattern in `src/meeting_minutes/system1/transcribe.py` — `BaseTranscriptionEngine` ABC, `FasterWhisperEngine` (existing), `WhisperCppEngine` (new), `get_transcription_engine()` factory, `get_available_engines()` status checker
    - _Requirements: 2.1, 2.2_
  - [x] 30.6 Add `OllamaConfig` to config with `base_url` and `timeout_seconds`, document engine/provider options in config comments
    - _Requirements: 13.1_
  - [x] 30.7 Add API endpoints `GET /api/config/hardware` and `GET /api/config/transcription-engines`, enable Ollama model fetching in `/api/config/provider-models`
  - [x] 30.8 Update frontend Settings page — transcription engine selector with install badges, Ollama status indicator, hardware-aware model recommendations, dynamic Ollama model dropdown
  - [x] 30.9 Add `[local-ai]` optional dependency group in pyproject.toml (`pywhispercpp`, `psutil`)
  - [x] 30.10 Update all documentation — README, specs (00-05), USER_GUIDE, design.md, requirements.md, tasks.md

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using `hypothesis`
- Unit tests validate specific examples and edge cases
- All property tests should run with `@settings(max_examples=100)`
