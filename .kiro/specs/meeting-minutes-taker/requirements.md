# Requirements Document

## Introduction

The Meeting Minutes Taker is a local-first, three-system pipeline application that captures meeting audio, generates intelligent meeting minutes using LLMs, and makes them searchable. The MVP focuses on: audio capture with local transcription (Whisper), general-purpose minutes generation via LLM, and SQLite-backed storage with CLI search. The three systems communicate via JSON files on a shared filesystem and can be triggered in automatic, semi-automatic, or manual pipeline modes.

## Glossary

- **Audio_Capture_Engine**: The component responsible for recording audio from system audio devices using `sounddevice`, including circular buffering, gain control, and voice activity detection.
- **Transcription_Engine**: The component that converts audio to text using local Whisper (`faster-whisper`) or cloud-based Amazon Transcribe.
- **Diarization_Engine**: The component that identifies and labels distinct speakers in audio using `pyannote.audio`.
- **Minutes_Generator**: The component that takes a transcript JSON and produces structured meeting minutes using an LLM (Anthropic Claude, OpenAI, or local model).
- **Prompt_Router**: The component that selects the appropriate prompt template based on meeting type classification.
- **Storage_Engine**: The SQLite-backed component that persists meetings, transcripts, minutes, action items, and decisions.
- **Search_Engine**: The SQLite FTS5-based component that provides full-text search across transcripts and minutes.
- **CLI_Interface**: The `typer`-based command-line interface for searching, browsing, and managing meetings.
- **Pipeline_Orchestrator**: The component that coordinates the three systems, supporting automatic, semi-automatic, and manual trigger modes.
- **Transcript_JSON**: The structured JSON output of System 1, containing metadata, speaker mappings, and timestamped transcript segments.
- **Minutes_JSON**: The structured JSON output of System 2, containing summary, sections, action items, decisions, and rendered markdown.
- **Meeting_Type**: A classification label (e.g., standup, decision_meeting, one_on_one) assigned to a meeting based on calendar metadata and transcript content.
- **Action_Item**: A task extracted from meeting minutes with description, owner, optional due date, and status.
- **Decision**: A decision recorded during a meeting with description and the person who made it.
- **Person_Entity**: A deduplicated person record maintained across meetings with name, email, and aliases.
- **FTS5**: SQLite Full-Text Search extension version 5, providing keyword search with BM25 ranking.

## Requirements

### Requirement 1: Audio Capture

**User Story:** As a meeting participant, I want to capture audio from virtual and physical meetings, so that the audio can be transcribed and turned into meeting minutes.

#### Acceptance Criteria

1. WHEN a user starts a recording via CLI command, THE Audio_Capture_Engine SHALL begin capturing audio from the configured system audio device at the configured sample rate (default 16kHz).
2. WHEN a user stops a recording via CLI command, THE Audio_Capture_Engine SHALL finalize the audio file and save it in FLAC format to the configured recordings directory.
3. WHILE recording is active, THE Audio_Capture_Engine SHALL write audio data to a circular buffer to prevent data loss during processing spikes.
4. WHILE recording is active AND no speech is detected for a configurable silence duration (default 5 minutes), THE Audio_Capture_Engine SHALL automatically stop the recording.
5. WHEN a recording is started, THE Audio_Capture_Engine SHALL generate a unique UUID as the meeting_id for the session.
6. WHEN a recording completes, THE Audio_Capture_Engine SHALL record metadata including meeting_id, start timestamp, end timestamp, duration, audio device name, and sample rate.
7. IF the audio device is unavailable or fails during recording, THEN THE Audio_Capture_Engine SHALL save any partial audio captured and mark the recording as incomplete.

### Requirement 2: Transcription

**User Story:** As a meeting participant, I want recorded audio to be transcribed into text, so that the transcript can be used to generate meeting minutes.

#### Acceptance Criteria

1. WHEN a recording completes, THE Transcription_Engine SHALL transcribe the audio file using the configured engine (local Whisper by default).
2. WHEN using local Whisper, THE Transcription_Engine SHALL use the `faster-whisper` library with the user-configured model size (default: medium).
3. WHEN transcription completes, THE Transcription_Engine SHALL produce word-level timestamps and per-word confidence scores for each segment.
4. WHEN transcription completes, THE Transcription_Engine SHALL output a Transcript_JSON file conforming to the schema version 1.0 in the configured transcripts directory.
5. IF transcription fails with the primary engine, THEN THE Transcription_Engine SHALL retry with the configured fallback engine and save the audio for later reprocessing.
6. WHEN a custom vocabulary file is configured, THE Transcription_Engine SHALL use the custom vocabulary to improve recognition of domain-specific terms.
7. WHEN transcription completes, THE Transcription_Engine SHALL detect and record the language of the audio in the Transcript_JSON metadata.

### Requirement 3: Speaker Diarization

**User Story:** As a meeting participant, I want speakers to be identified and labeled in the transcript, so that the minutes can attribute statements to specific people.

#### Acceptance Criteria

1. WHEN diarization is enabled in configuration, THE Diarization_Engine SHALL identify and label distinct speakers in the audio using `pyannote.audio`.
2. WHEN diarization completes, THE Diarization_Engine SHALL assign a speaker label (e.g., SPEAKER_00, SPEAKER_01) to each transcript segment.
3. WHEN diarization completes, THE Diarization_Engine SHALL record the total number of distinct speakers detected in the Transcript_JSON metadata.
4. IF diarization fails, THEN THE Transcription_Engine SHALL produce the transcript without speaker labels and mark diarization as failed in the Transcript_JSON.

### Requirement 4: Transcript JSON Output

**User Story:** As a system integrator, I want the transcript output to follow a well-defined JSON schema, so that System 2 can reliably consume it.

#### Acceptance Criteria

1. THE Transcription_Engine SHALL output Transcript_JSON containing: schema_version, meeting_id, metadata (timestamps, duration, platform, language, engine, model, audio_file, device), speakers array, meeting_type, and transcript (segments array and full_text).
2. WHEN producing Transcript_JSON, THE Transcription_Engine SHALL include a processing block with created_at timestamp, processing_time_seconds, and pipeline_version.
3. THE Transcription_Engine SHALL serialize the Transcript_JSON using UTF-8 encoding.
4. FOR ALL valid Transcript_JSON files, parsing the JSON then re-serializing it SHALL produce a semantically equivalent JSON document (round-trip property).

### Requirement 5: Minutes Generation

**User Story:** As a meeting participant, I want meeting minutes to be automatically generated from the transcript, so that I have a structured summary without manual note-taking.

#### Acceptance Criteria

1. WHEN a Transcript_JSON file is provided, THE Minutes_Generator SHALL validate the JSON against the transcript schema before processing.
2. WHEN processing a valid transcript, THE Minutes_Generator SHALL replace speaker labels (e.g., SPEAKER_00) with actual participant names from the speakers array.
3. WHEN processing a transcript, THE Minutes_Generator SHALL construct a prompt using the system prompt, meeting type template, context block (metadata, attendees), and the transcript text.
4. WHEN the LLM returns a response, THE Minutes_Generator SHALL parse the response to extract: summary, sections, action_items (with owner and optional due date), decisions (with decision maker), and key_topics.
5. WHEN minutes generation completes, THE Minutes_Generator SHALL output both a Minutes_JSON file and a rendered Markdown file to the configured minutes directory.
6. IF the LLM API is unavailable, THEN THE Minutes_Generator SHALL queue the transcript for retry and log the failure.
7. IF the LLM response is malformed or fails parsing, THEN THE Minutes_Generator SHALL retry with an adjusted prompt up to the configured retry limit (default: 3 attempts).

### Requirement 6: Meeting Type Routing

**User Story:** As a meeting participant, I want the system to select the appropriate prompt template based on meeting type, so that the generated minutes match the structure expected for that type of meeting.

#### Acceptance Criteria

1. WHEN a transcript has a meeting_type with confidence >= 0.7, THE Prompt_Router SHALL select the corresponding prompt template for that meeting type.
2. WHEN a transcript has a meeting_type with confidence < 0.7, THE Prompt_Router SHALL run a secondary LLM-based classification on the first 10 minutes of transcript text combined with calendar metadata.
3. WHEN no matching template exists for the classified meeting type, THE Prompt_Router SHALL fall back to the general-purpose ("other") template.
4. WHEN a user provides a meeting type override via CLI flag, THE Prompt_Router SHALL use the overridden type regardless of the classified type and confidence.
5. THE Prompt_Router SHALL support at minimum these meeting types: standup, one_on_one, decision_meeting, customer_meeting, brainstorm, retrospective, planning, and other.

### Requirement 7: Minutes JSON Output

**User Story:** As a system integrator, I want the minutes output to follow a well-defined JSON schema, so that System 3 can reliably consume and index it.

#### Acceptance Criteria

1. THE Minutes_Generator SHALL output Minutes_JSON containing: schema_version, meeting_id, minutes_id, generated_at, meeting_type, metadata (title, date, duration, attendees, organizer), summary, sections array, action_items array, decisions array, key_topics array, and minutes_markdown.
2. WHEN producing Minutes_JSON, THE Minutes_Generator SHALL include an llm block with provider, model, tokens_used (input and output), cost_usd, and processing_time_seconds.
3. THE Minutes_Generator SHALL serialize the Minutes_JSON using UTF-8 encoding.
4. FOR ALL valid Minutes_JSON files, parsing the JSON then re-serializing it SHALL produce a semantically equivalent JSON document (round-trip property).

### Requirement 8: Quality Assurance for Generated Minutes

**User Story:** As a meeting participant, I want the generated minutes to be checked for quality, so that I can trust the accuracy and completeness of the output.

#### Acceptance Criteria

1. WHEN minutes are generated, THE Minutes_Generator SHALL verify that every speaker present in the transcript appears in the generated minutes.
2. WHEN minutes are generated, THE Minutes_Generator SHALL verify that the minutes length is between 10% and 30% of the original transcript length.
3. WHEN minutes are generated, THE Minutes_Generator SHALL flag any names, dates, or numbers in the minutes that do not appear in the original transcript as potential hallucinations.
4. WHEN a quality check fails, THE Minutes_Generator SHALL log a warning and include the quality check results in the Minutes_JSON output.

### Requirement 9: Meeting Storage

**User Story:** As a user, I want meetings, transcripts, and minutes to be stored in a local database, so that I can retrieve and search them later.

#### Acceptance Criteria

1. WHEN a Minutes_JSON file is ingested, THE Storage_Engine SHALL parse and validate the JSON, then store the Meeting, Transcript, Minutes, Action_Items, and Decisions records in the SQLite database.
2. WHEN storing a meeting, THE Storage_Engine SHALL extract or create Person_Entity records for each attendee and link them to the meeting.
3. WHEN a meeting with the same meeting_id already exists, THE Storage_Engine SHALL upsert (overwrite) the existing records rather than creating duplicates.
4. WHEN storage completes, THE Storage_Engine SHALL update the FTS5 full-text search index with the transcript full_text and minutes markdown content.
5. IF a database write fails, THEN THE Storage_Engine SHALL write the ingested data to a local fallback file and log the error for later reconciliation.

### Requirement 10: Full-Text Search

**User Story:** As a user, I want to search across all my meeting transcripts and minutes using keywords, so that I can quickly find relevant meetings.

#### Acceptance Criteria

1. WHEN a user submits a search query, THE Search_Engine SHALL execute a full-text search using SQLite FTS5 across indexed transcript and minutes content.
2. WHEN returning search results, THE Search_Engine SHALL rank results using BM25 scoring.
3. WHEN a search query contains a phrase in double quotes, THE Search_Engine SHALL perform exact phrase matching.
4. WHEN a search query contains boolean operators (AND, OR, NOT), THE Search_Engine SHALL apply the boolean logic to filter results.
5. WHEN a search query includes a date range filter (after: and/or before:), THE Search_Engine SHALL restrict results to meetings within the specified date range.
6. WHEN a search query includes a meeting type filter (type:), THE Search_Engine SHALL restrict results to meetings of the specified type.
7. WHEN no results match the query, THE Search_Engine SHALL return an empty result set with zero count.

### Requirement 11: CLI Interface

**User Story:** As a user, I want a command-line interface to search, browse, and manage my meetings, so that I can interact with the system without a graphical UI.

#### Acceptance Criteria

1. WHEN a user runs `mm search <query>`, THE CLI_Interface SHALL execute a full-text search and display matching meetings with title, date, type, and a snippet of the matching content.
2. WHEN a user runs `mm search` with `--type` or `--after`/`--before` flags, THE CLI_Interface SHALL pass the filters to the Search_Engine and display filtered results.
3. WHEN a user runs `mm list`, THE CLI_Interface SHALL display a chronological list of recent meetings with meeting_id, title, date, type, and status.
4. WHEN a user runs `mm show <meeting_id>`, THE CLI_Interface SHALL display the full rendered meeting minutes for the specified meeting.
5. WHEN a user runs `mm actions`, THE CLI_Interface SHALL display all open action items across meetings with description, owner, due date, and status.
6. WHEN a user runs `mm actions --owner <email>`, THE CLI_Interface SHALL filter action items to those assigned to the specified person.
7. WHEN a user runs `mm actions complete <action_id>`, THE CLI_Interface SHALL update the action item status to "done" in the database.
8. WHEN a user runs `mm delete <meeting_id>`, THE CLI_Interface SHALL remove all data associated with the meeting (audio, transcript, minutes, action items, decisions) from the database and filesystem.
9. IF a user provides an invalid meeting_id or action_id, THEN THE CLI_Interface SHALL display a descriptive error message and exit with a non-zero status code.

### Requirement 12: Pipeline Orchestration

**User Story:** As a user, I want to run the three systems as a coordinated pipeline, so that meetings are processed end-to-end without manual intervention.

#### Acceptance Criteria

1. WHEN pipeline_mode is set to "automatic", THE Pipeline_Orchestrator SHALL trigger System 2 immediately after System 1 completes, and trigger System 3 immediately after System 2 completes.
2. WHEN pipeline_mode is set to "semi_automatic", THE Pipeline_Orchestrator SHALL run System 1 automatically but wait for a manual user trigger before running System 2 and System 3.
3. WHEN pipeline_mode is set to "manual", THE Pipeline_Orchestrator SHALL require a separate manual trigger for each system.
4. WHEN a system in the pipeline completes, THE Pipeline_Orchestrator SHALL write the output file to the appropriate directory and emit an event (filesystem-based) indicating completion.
5. WHEN a user runs `mm reprocess <meeting_id>`, THE Pipeline_Orchestrator SHALL re-run the entire pipeline for the specified meeting, overwriting previous outputs.

### Requirement 13: Configuration Management

**User Story:** As a user, I want to configure the system through a YAML configuration file, so that I can customize audio devices, transcription models, LLM providers, and storage paths.

#### Acceptance Criteria

1. THE Pipeline_Orchestrator SHALL load configuration from a YAML file at the configured path (default: `config/config.yaml`).
2. WHEN the configuration file is missing or contains invalid YAML, THE Pipeline_Orchestrator SHALL report a descriptive error and exit with a non-zero status code.
3. WHEN a configuration value is not specified, THE Pipeline_Orchestrator SHALL use documented default values for all configurable settings.
4. THE Pipeline_Orchestrator SHALL validate that required configuration fields (data_dir, transcription engine, LLM provider) are present and well-formed at startup.

### Requirement 14: Logging and Observability

**User Story:** As a developer, I want structured logging across all three systems, so that I can diagnose issues and monitor pipeline health.

#### Acceptance Criteria

1. THE Pipeline_Orchestrator SHALL produce structured JSON log entries with timestamp, log level, system name, meeting_id (when available), and message.
2. WHILE processing a meeting, each system SHALL include the meeting_id as a correlation_id in all log entries for that meeting.
3. THE Pipeline_Orchestrator SHALL support configurable log levels (DEBUG, INFO, WARNING, ERROR) via the configuration file.
4. WHEN an error occurs in any system, THE Pipeline_Orchestrator SHALL log the error with full context including the meeting_id, system name, and error details.

### Requirement 15: Data Deletion

**User Story:** As a user, I want to completely delete all data for a meeting, so that I can manage my data and comply with privacy needs.

#### Acceptance Criteria

1. WHEN a user requests deletion of a meeting by meeting_id, THE Storage_Engine SHALL remove the meeting record, transcript, minutes, action items, decisions, and attendee links from the database.
2. WHEN a user requests deletion of a meeting, THE Storage_Engine SHALL delete the associated audio file, transcript JSON file, and minutes files from the filesystem.
3. WHEN a user requests deletion of a meeting, THE Storage_Engine SHALL remove the meeting content from the FTS5 search index.
4. IF any file or record does not exist during deletion, THEN THE Storage_Engine SHALL skip the missing item, log a warning, and continue deleting remaining items.
