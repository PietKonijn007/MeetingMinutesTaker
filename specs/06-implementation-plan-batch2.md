# Implementation Plan — Batch 2

Selected features: S4, S7, R1, R2, R3, R4, R7, N6, N7, N11, A4, P1, P2, U1

## Implementation Order

### Phase 1: Foundation (logging, error handling, config)
1. **R1** — Fix silent exception handling
2. **R2** — Replace print() with structured logging
3. **S7** — Tighten CORS
4. **R3** — Configurable retention policies (with GUI)

### Phase 2: Performance
5. **P1** — Distil-Whisper for faster transcription
6. **P2** — Apple Silicon Metal acceleration

### Phase 3: Resilience
7. **R4** — Pipeline retry with backoff
8. **R7** — Auto-backup before migrations
9. **S4** — Encryption at rest

### Phase 4: Note-taking Intelligence
10. **N6** — Live note-taking on Record page (Granola-style)
11. **N7** — Sentiment analysis per speaker
12. **N11** — Automatic meeting type refinement
13. **A4** — Meeting effectiveness score

### Phase 5: UI
14. **U1** — Meeting search with filters in calendar view

### Phase 6: Documentation
15. Update README, USER_GUIDE, specs, GUI docs

## Detailed Specifications

### S4: Encryption at Rest
- Add `security.encryption_enabled: bool` and `security.encryption_key: str` to config
- Use `cryptography.fernet` for symmetric encryption
- Encrypt: audio files, transcript JSON, minutes JSON
- Decrypt on read, encrypt on write
- Key derived from user passphrase via PBKDF2
- Settings page: enable toggle, passphrase input

### S7: Tighten CORS
- Read allowed origins from config instead of hardcoding
- Default: localhost only
- Config: `api.cors_origins: ["http://localhost:8080"]`

### R1: Fix Silent Exception Handling
- WebSocket: log errors before closing
- Device detection: log warnings on failure
- Template loading: log which template failed
- Upload parsing: include parse error details

### R2: Structured Logging
- Replace all `print()` and `print(file=sys.stderr)` with `get_logger()`
- Consistent format: timestamp, level, module, meeting_id, message
- Config: `log_level` already exists, use it

### R3: Configurable Retention Policies
- Config: `retention.audio_days: 90`, `retention.transcript_days: -1`, `retention.minutes_days: -1`
- Background cleanup: check on server start + hourly
- Settings page: retention inputs per data type
- CLI: `mm cleanup` to run manually

### R4: Pipeline Retry with Backoff
- If transcription fails: retry up to 2 times with 5s backoff
- If LLM generation fails: retry up to 3 times with exponential backoff
- If ingestion fails: retry once
- Store retry count in pipeline job status
- Show retry count in UI

### R7: Auto-Backup Before Migrations
- In `mm init` and alembic migration: auto-backup DB first
- Log: "Backed up database before migration: backups/pre_migration_YYYYMMDD.db"

### N6: Granola-Style Note Enhancement (Record Page)
- Add a "Meeting Notes" textarea to the Record page (visible during recording)
- User types rough bullet notes while the meeting is happening
- When recording stops: user's notes + transcript are both sent to the LLM
- LLM prompt: "Enhance these rough meeting notes using the full transcript. Keep the user's structure and emphasis but fill in details, quotes, and specifics from the transcript."
- The enhanced notes become the meeting minutes
- If no notes taken: falls back to normal template-based generation

### N7: Sentiment Analysis Per Speaker
- After diarization + transcription, analyze sentiment per speaker per segment
- Use the structured output tool_use to include `speaker_sentiments` in StructuredMinutesResponse
- Fields: speaker name, overall sentiment (positive/neutral/negative/mixed), notable moments
- Display in meeting detail as colored indicators next to speaker names

### N11: Automatic Meeting Type Refinement
- After generating minutes, compare the actual content against the classified type
- If the content doesn't match (e.g., classified as "other" but has clear 1:1 patterns), suggest reclassification
- Add to pipeline: `_maybe_reclassify()` after generation
- If reclassified: re-run generation with the correct template (optional, configurable)

### A4: Meeting Effectiveness Score
- Already extracted by StructuredMinutesResponse: `meeting_effectiveness.had_clear_agenda`, `decisions_made`, `action_items_assigned`, `unresolved_items`
- Compute a 1-5 score: agenda (1pt) + decisions>0 (1pt) + actions>0 (1pt) + low unresolved (1pt) + short duration relative to content (1pt)
- Display as stars or score in meeting detail header
- Track over time in Stats page

### P1: Distil-Whisper
- Add config option: `transcription.whisper_model: "distil-medium.en"` or `"distil-large-v3"`
- Distil-Whisper is 6x faster, within 1% WER
- In TranscriptionEngine: detect "distil-" prefix and use appropriate model name
- Settings page: add distil variants to model dropdown

### P2: Apple Silicon Metal Acceleration
- In TranscriptionEngine._load_model(): detect Apple Silicon and set `device="auto"`, `compute_type="float16"`
- faster-whisper supports Metal via CTranslate2
- Fallback to CPU if Metal not available

### U1: Meeting Search with Filters
- Add a search bar to the calendar page left panel (above the calendar)
- Search filters: text query, meeting type, attendee name
- Uses existing `/api/search` endpoint
- Results replace the calendar day list with search results
- Click a result to show its detail in the right panel
- Clear search to return to calendar view
