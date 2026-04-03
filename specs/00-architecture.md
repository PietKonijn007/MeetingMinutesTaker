# Meeting Minutes Taker — System Architecture

## Overview

The Meeting Minutes Taker is a three-system pipeline that captures meeting audio, generates intelligent meeting minutes, and makes them searchable. The systems are loosely coupled, communicating through well-defined JSON interfaces and a shared filesystem.

```
┌─────────────────────┐     JSON      ┌─────────────────────┐     JSON      ┌─────────────────────┐
│                     │   Transcript   │                     │   Minutes     │                     │
│  System 1           │──────────────▶│  System 2           │──────────────▶│  System 3           │
│  Recording &        │               │  Minutes            │               │  Storage &          │
│  Transcription      │               │  Generation         │               │  Search             │
│                     │               │                     │               │                     │
│  • Audio capture    │               │  • Type routing     │               │  • Database         │
│  • Transcription    │               │  • Prompt selection │               │  • Full-text search │
│  • Diarization      │               │  • LLM generation   │               │  • REST API (:8080) │
│  • Metadata         │               │  • Quality checks   │               │  • Svelte Web UI    │
│                     │               │                     │               │  • Analytics        │
└─────────────────────┘               └─────────────────────┘               └─────────────────────┘
        │                                                                           │
        │                     ┌─────────────────────┐                               │
        └────────────────────▶│  Shared Storage      │◀──────────────────────────────┘
                              │  • Audio files       │
                              │  • Transcript JSON   │
                              │  • Minutes JSON/MD   │
                              │  • Config (YAML)     │
                              │  • .env file         │
                              └─────────────────────┘
```

---

## 1. Data Flow

### 1.1 End-to-End Pipeline

```
Meeting Occurs
    │
    ▼
[System 1: Record & Transcribe]
    │
    ├── 1. Capture audio from system audio devices
    │   (virtual loopback for virtual meetings, mic for physical)
    │
    ├── 2. Run transcription (Whisper local or Amazon Transcribe)
    │
    ├── 3. Run speaker diarization (pyannote.audio)
    │
    ├── 4. Enrich with calendar metadata
    │   (Google Calendar / Outlook → attendees, title, type)
    │
    ├── 5. Classify meeting type
    │   (standup, 1:1, team meeting, decision meeting, client call, etc.)
    │   Uses LLM classifier (Claude Haiku) when keyword confidence < 0.7
    │   Reads template descriptions for template-aware classification
    │
    ├── 6. Map speakers to attendees
    │
    └── 7. Output: transcript.json + audio file
            │
            ▼
[System 2: Generate Minutes]
    │
    ├── 1. Ingest transcript.json
    │
    ├── 2. Route to appropriate prompt template based on meeting_type
    │   ┌──────────────────────────────────────────────┐
    │   │  standup        → standup_template.md         │
    │   │  one_on_one     → one_on_one_template.md      │
    │   │  team_meeting   → team_meeting_template.md    │
    │   │  decision       → decision_template.md        │
    │   │  client_call    → client_call_template.md     │
    │   │  brainstorm     → brainstorm_template.md      │
    │   │  retrospective  → retrospective_template.md   │
    │   │  ...            → ...                         │
    │   │  other          → general_template.md         │
    │   └──────────────────────────────────────────────┘
    │
    ├── 3. Construct prompt (system + template + context + transcript)
    │
    ├── 4. Send to LLM (Claude / GPT / local model)
    │      Primary: structured JSON output via Anthropic tool_use
    │      Fallback: text response + regex parsing
    │
    ├── 5. Parse response, extract action items & decisions
    │
    ├── 6. Quality checks (completeness, hallucination guard)
    │
    └── 7. Output: minutes.json + minutes.md
            │
            ▼
[System 3: Store & Search]
    │
    ├── 1. Ingest minutes.json
    │
    ├── 2. Store in database (SQLite / PostgreSQL)
    │   ├── Meeting record
    │   ├── Transcript (full text + segments)
    │   ├── Minutes (markdown + structured sections)
    │   ├── Action items (with owner, status, due date)
    │   ├── Decisions
    │   ├── Person entities (deduplicated across meetings)
    │   └── Topic entities (extracted and linked)
    │
    ├── 3. Index for full-text search (FTS5 / tsquery)
    │
    ├── 4. Generate embeddings for semantic search
    │   (meeting summaries, sections, action items)
    │
    ├── 5. Update cross-meeting links
    │   (recurring meeting series, action item tracking)
    │
    └── 6. Serve via API, CLI, and Web UI
```

### 1.2 Interface Contracts

#### System 1 → System 2: Transcript JSON

```
File: transcripts/{meeting_id}.json
Schema: See System 1 spec, Section 4.1
Key fields consumed by System 2:
  - meeting_type + confidence
  - calendar.title, calendar.attendees
  - speakers[] mapping
  - transcript.full_text
  - transcript.segments[]
```

#### System 2 → System 3: Minutes JSON

```
File: minutes/{meeting_id}.json
Schema: See System 2 spec, Section 5.2
Key fields consumed by System 3:
  - meeting_id (links back to System 1 data)
  - metadata (title, date, attendees, type)
  - summary
  - sections[]
  - action_items[]
  - decisions[]
  - key_topics[]
  - minutes_markdown
```

---

## 2. System Integration Points

### 2.1 Trigger Modes

The pipeline can run in three modes:

| Mode | Description | Use Case |
|------|-------------|----------|
| **Automatic** | System 1 triggers System 2 on completion; System 2 triggers System 3 | Hands-off operation |
| **Semi-automatic** | System 1 runs automatically; user reviews transcript, then manually triggers System 2+3 | When transcript review is desired |
| **Manual** | Each system triggered independently via CLI | Development, debugging, re-processing |

### 2.2 Event-Driven Communication

```
System 1 finishes recording + transcription
    │
    ├── Writes transcript.json to transcripts/ directory
    ├── Emits event: "transcript.ready" { meeting_id, path }
    │
    ▼
File watcher / Event bus picks up event
    │
    ▼
System 2 receives event
    │
    ├── Reads transcript.json
    ├── Generates minutes
    ├── Writes minutes.json to minutes/ directory
    ├── Emits event: "minutes.ready" { meeting_id, path }
    │
    ▼
File watcher / Event bus picks up event
    │
    ▼
System 3 receives event
    │
    ├── Reads minutes.json
    ├── Indexes in database + search
    ├── Emits event: "minutes.indexed" { meeting_id }
    │
    ▼
Post-processing hooks fire
    ├── Manually share minutes (user-initiated, never automatic)
    ├── Post summary to Slack
    ├── Create tasks in project management tool
    └── Update calendar event with minutes link
```

### 2.3 Event Bus Options

| Option | Description | Complexity |
|--------|-------------|------------|
| **Filesystem watcher** | `watchdog` monitors directories for new files | Low — simplest, local-only |
| **SQLite queue** | Write events to a SQLite table, poll for new events | Low — reliable, local |

**Default recommendation**: Filesystem watcher for single-user local deployment.

---

## 3. Shared Configuration

### 3.1 Directory Structure

```
~/MeetingMinutesTaker/
├── .env                         # API keys (optional, takes priority over env vars)
├── config/
│   ├── config.yaml              # Main configuration
│   └── vocabulary.txt           # Custom vocabulary for transcription
│
├── templates/                   # Jinja2 prompt templates
│   ├── standup.md.j2
│   ├── decision_meeting.md.j2
│   └── ...
│
├── data/
│   ├── recordings/              # Audio files (System 1 output)
│   │   ├── 2026-03-28_daily-standup.flac
│   │   └── ...
│   ├── transcripts/             # Transcript JSON (System 1 output → System 2 input)
│   │   ├── abc123.json
│   │   └── ...
│   ├── minutes/                 # Minutes JSON + MD (System 2 output → System 3 input)
│   │   ├── abc123.json
│   │   ├── abc123.md
│   │   └── ...
│   └── exports/                 # Exported files (PDF, DOCX, etc.)
│
├── db/
│   ├── meetings.db              # SQLite database (System 3)
│   └── vectors.db               # Vector store (System 3)
│
├── models/
│   ├── whisper/                  # Downloaded Whisper models
│   └── embeddings/              # Downloaded embedding models
│
├── voice-profiles/              # Speaker voice profiles (System 1)
│
├── logs/
│   ├── system1.log
│   ├── system2.log
│   └── system3.log
│
└── scripts/
    ├── start.sh                 # Start all systems
    ├── stop.sh                  # Stop all systems
    └── reprocess.sh             # Re-process a meeting
```

### 3.2 Unified Configuration File

```yaml
# config/config.yaml

data_dir: ~/MeetingMinutesTaker/data
log_level: INFO

pipeline:
  mode: automatic                  # automatic | semi_automatic | manual

# System 1 settings
recording:
  audio_device: "MeetingCapture"
  sample_rate: 16000
  format: flac
  auto_stop_silence_minutes: 5

transcription:
  primary_engine: whisper
  whisper_model: medium
  language: auto
  custom_vocabulary: null

diarization:
  enabled: true
  engine: pyannote

# System 2 settings
generation:
  templates_dir: templates         # Jinja2 prompt templates directory
  llm:
    primary_provider: anthropic
    model: claude-sonnet-4-6-20250514
    fallback_provider: null        # null = disabled, or "openai"
    fallback_model: gpt-4o
    temperature: 0.2
    max_output_tokens: 4096
    retry_attempts: 3
    timeout_seconds: 120

# System 3 settings
storage:
  database: sqlite
  sqlite_path: db/meetings.db
```

---

## 4. Deployment Topologies

### 4.1 Single-User Local (Default)

```
┌──────────────────────────────────────────────┐
│  Local Machine                               │
│                                              │
│  System 1 ──▶ System 2 ──▶ System 3         │
│  (process)    (process)    (process + API)   │
│                                              │
│  SQLite DB    Local Whisper   Web UI :8080   │
│  Audio files  Anthropic API  API :8080       │
└──────────────────────────────────────────────┘
```

- All three systems run as local processes
- Filesystem-based communication
- SQLite for storage
- Minimal dependencies, no external services required (when using local models)


---

## 5. Cross-Cutting Concerns

### 5.1 Error Handling

| Failure | Behavior |
|---------|----------|
| Audio capture fails mid-recording | Save partial audio, mark transcript as incomplete |
| Transcription fails | Retry with fallback engine; save audio for later reprocessing |
| LLM API unavailable | Queue for retry; fall back to alternative provider |
| LLM response malformed | Retry with adjusted prompt; fall back to general template |
| Database write fails | Write to local file as fallback; reconcile on recovery |
| Embedding generation fails | Index without embeddings; retry in background |

### 5.2 Logging & Observability

- Structured JSON logging across all three systems
- Shared `correlation_id` (= `meeting_id`) across the pipeline
- Log levels: DEBUG, INFO, WARNING, ERROR
- Optional metrics export (Prometheus format) for:
  - Pipeline throughput (meetings processed/day)
  - Processing latency per stage
  - LLM token usage and cost
  - Error rates

### 5.3 Idempotency & Reprocessing

- Each meeting has a stable UUID (`meeting_id`) assigned by System 1
- Re-running any system for the same `meeting_id` overwrites previous output (upsert)
- CLI commands available:
  - `mm retranscribe <meeting_id>` — re-run System 1 transcription
  - `mm regenerate <meeting_id>` — re-run System 2 generation
  - `mm reindex <meeting_id>` — re-run System 3 indexing
  - `mm reprocess <meeting_id>` — re-run entire pipeline

### 5.4 Privacy & Security

- **Principle**: Local-first, data stays on user's machine by default
- **Encryption**: Optional at-rest encryption for audio, transcripts, and database
- **Cloud data**: When using cloud APIs, data is transmitted over TLS; check provider data retention policies
- **Access control**: Single-user local deployment; no multi-user access control needed
- **Consent**: Recording indicator always visible; optional consent prompt before recording
- **Data deletion**: `mm delete <meeting_id>` removes all data (audio, transcript, minutes, embeddings)

---

## 6. Technology Summary

| Layer | Technology |
|-------|-----------|
| **Language** | Python 3.11+ |
| **Audio** | `sounddevice`, BlackHole (macOS), WASAPI (Windows) |
| **Transcription** | `faster-whisper` |
| **Diarization** | `pyannote.audio` |
| **LLM** | `anthropic` SDK (primary), `openai` SDK (fallback) |
| **Database** | SQLite + SQLAlchemy |
| **Search** | SQLite FTS5 |
| **API** | FastAPI + uvicorn |
| **Web UI** | Svelte + SvelteKit + Tailwind CSS |
| **CLI** | `typer` |
| **Config** | YAML (`pyyaml`) |
| **Events** | `watchdog` (filesystem) |
| **Packaging** | `pip` + `pyproject.toml`, Docker optional |

---

## 7. Development Phases

### Phase 1: MVP (Core Pipeline)
- System 1: Audio capture + local Whisper transcription + basic metadata
- System 2: General-purpose minutes template + Claude API integration
- System 3: SQLite storage + CLI search + basic full-text search
- File-based pipeline, manual trigger

### Phase 2: Intelligence
- Meeting type classification and type-specific templates
- Speaker diarization + calendar integration + speaker mapping
- Semantic search with embeddings
- Automatic pipeline triggering

### Phase 3: Polish & Integrations
- Web UI for browsing and search
- Action item tracking across meetings
- Export to Google Docs, Confluence, Slack
- Analytics dashboard
- Custom prompt templates

### Phase 4: Polish
- Docker packaging for easy setup
- Additional export integrations
- Analytics dashboard refinements
