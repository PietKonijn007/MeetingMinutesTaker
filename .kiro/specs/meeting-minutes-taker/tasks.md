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
  - [x] 5.7 Implement LLMClient in `src/meeting_minutes/system2/llm_client.py` — async Anthropic/OpenAI API calls, retry with exponential backoff, fallback provider, token usage tracking, cost calculation
    - _Requirements: 5.6, 5.7_
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

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using `hypothesis`
- Unit tests validate specific examples and edge cases
- All property tests should run with `@settings(max_examples=100)`
