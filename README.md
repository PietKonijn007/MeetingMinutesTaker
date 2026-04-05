# Meeting Minutes Taker

A local-first system that records your meetings, generates intelligent meeting minutes using LLMs, and makes them searchable. Works with Zoom, Teams, Slack, Google Meet, and in-person meetings.

## How It Works

```
Record Audio ──► Transcribe ──► Generate Minutes ──► Store & Search
  (System 1)      (Whisper)      (Claude / GPT)       (SQLite + FTS5)
```

**System 1 — Recording & Transcription**: Captures audio from virtual and physical meetings via system audio loopback (BlackHole on macOS), transcribes with Whisper (including Distil-Whisper models with Metal acceleration on Apple Silicon), identifies speakers with pyannote.audio, enriches with calendar metadata, and supports live note-taking (speaker names, notes, custom LLM instructions) during recording.

**System 2 — Minutes Generation**: Auto-detects meeting type using an LLM classifier (Claude Haiku) with meeting type refinement, routes transcripts to meeting-type-specific prompt templates, generates structured minutes via LLM (Anthropic Claude `claude-sonnet-4-6` by default) using tool_use for guaranteed JSON output (with text+regex fallback), extracts action items and decisions with per-speaker sentiment analysis and meeting effectiveness scoring, and runs quality checks. Supports custom LLM instructions provided during recording.

**System 3 — Storage & Search**: Stores everything in SQLite with full-text search (FTS5), provides a CLI for searching, browsing, and managing meetings and action items. Supports encryption at rest, configurable retention policies, and in-calendar search with filters.

## Supported Meeting Types

| Type | Template Focus |
|------|---------------|
| `standup` | Per-person Done / Today / Blockers |
| `one_on_one` | Discussion topics, feedback, career development |
| `customer_meeting` | Client requests, commitments, timeline |
| `decision_meeting` | Options, pros/cons, decision, rationale |
| `brainstorm` | Ideas generated, themes, top ideas |
| `retrospective` | Went well, didn't go well, improvements |
| `team_meeting` | Decisions, financial review, blockers, strategic updates |
| `planning` | Goals, tasks, estimates, risks |
| `general` | Summary, discussion points, decisions, actions |

## Quick Start

### Prerequisites

- Python 3.11+
- macOS 12+ (recommended: macOS 14+ on Apple Silicon)
- [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole) for virtual meeting capture

### Install

```bash
git clone https://github.com/PietKonijn007/MeetingMinutesTaker.git
cd MeetingMinutesTaker
pip install -e ".[dev]"
mm init
```

### Set API keys

```bash
export ANTHROPIC_API_KEY="sk-ant-..."    # Required for minutes generation
export HF_TOKEN="hf_..."                 # Required for speaker diarization
export OPENAI_API_KEY="sk-..."           # Optional fallback
```

Or create a `.env` file at the project root (takes priority over environment variables):

```
ANTHROPIC_API_KEY=sk-ant-...
HF_TOKEN=hf_...
OPENAI_API_KEY=sk-...
```

### Configure audio

Set up BlackHole with two virtual devices (see [User Guide](docs/USER_GUIDE.md#3-audio-setup-macos) for step-by-step instructions):

- Set your system output to **Meeting Output** (Multi-Output Device that sends audio to speakers + BlackHole)
- In the Meeting Minutes app, select **Meeting Capture** (Aggregate Device combining mic + BlackHole) as input, or use auto-detect which prefers MeetingCapture aggregate devices automatically

```yaml
# config/config.yaml
recording:
  audio_device: "Meeting Capture"
```

Leave audio settings in Zoom/Meet/Teams at their defaults — the system-level routing handles everything.

### Record a meeting

```bash
mm record start       # Begin recording
# ... attend your meeting ...
mm record stop        # Stop, transcribe, generate minutes, index
```

### Search and browse

```bash
mm search "database migration"                    # Full-text search
mm search "budget" --type decision_meeting        # Filter by type
mm search "sprint" --after 2026-03-01             # Filter by date
mm list                                           # Recent meetings
mm show <meeting_id>                              # View meeting details
mm actions                                        # Open action items
mm actions --owner alice@company.com              # Filter by owner
mm actions complete <action_id>                   # Mark done
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `mm init` | Create database and data directories |
| `mm record start` | Start recording |
| `mm record stop` | Stop recording and process |
| `mm search <query>` | Full-text search (supports `--type`, `--after`, `--before`) |
| `mm list` | List meetings (supports `--person`, `--limit`) |
| `mm show <id>` | Show meeting details and minutes |
| `mm actions` | List open action items (supports `--owner`, `--overdue`) |
| `mm actions complete <id>` | Mark action item as done |
| `mm generate <id>` | Generate minutes from transcript |
| `mm reprocess <id>` | Re-run full pipeline for a meeting |
| `mm delete <id>` | Delete meeting and all associated data |
| `mm cleanup` | Run retention policy cleanup (delete expired data) |
| `mm generate-key` | Generate a new encryption key for at-rest encryption |
| `mm serve` | Start the web UI + API server (supports `--host`, `--port`) |

## Web UI

A browser-based interface built with Svelte + Tailwind CSS at `localhost:8080` with calendar view, action items, decisions, people, stats, recording controls, template manager, and settings.

```bash
# Start the server
mm serve

# Open in your browser
open http://localhost:8080
```

**Pages**: Meetings (calendar view with day list + inline detail + search with filters), Meeting Detail, Action Items, Decisions, People, Stats (charts), Record (live waveform + concurrent pipeline status + live note-taking), Templates (view/edit/create prompt templates), Settings (including Security, Retention, and CORS config).

**Features**: Dark mode, full-text search with `Cmd+K`, in-calendar search with type filter chips, keyboard navigation, responsive layout, meeting type color coding, WebSocket-based real-time updates, concurrent pipeline processing (record a new meeting while the previous one processes in background), auto-detect capture device, auto-save recovery every 5 minutes during recording, live note-taking during recording (speaker names, notes, custom LLM instructions), encryption at rest, retention policies with automatic cleanup.

**Development** (with hot reload):
```bash
mm serve                          # API on :8080
cd web && npm install && npm run dev   # Svelte on :3000, proxies /api → :8080
```

## REST API

The backend is a FastAPI application serving at `:8080` with 32 routes covering meetings, search, action items, decisions, people, stats, recording, and configuration. Auto-generated interactive API documentation is available at [http://localhost:8080/docs](http://localhost:8080/docs).

## Configuration

Configuration lives at `config/config.yaml` (fallback: `~/.meeting-minutes/config.yaml`).

Key settings:

```yaml
pipeline:
  mode: automatic              # automatic | semi_automatic | manual

recording:
  audio_device: "Meeting Capture"
  sample_rate: 16000
  auto_stop_silence_minutes: 5

transcription:
  whisper_model: medium        # tiny | base | small | medium | large-v3

diarization:
  enabled: true

generation:
  llm:
    primary_provider: anthropic
    model: claude-sonnet-4-6-20250514
    temperature: 0.2

storage:
  sqlite_path: db/meetings.db

security:
  encryption_enabled: false
  encryption_key_path: null       # Path to Fernet encryption key file

api:
  cors_origins: ["*"]
  host: "127.0.0.1"
  port: 8080

retention:
  enabled: false
  audio_retention_days: 90        # Delete audio files after N days
  transcript_retention_days: null  # null = keep forever
  minutes_retention_days: null     # null = keep forever
```

See the full [configuration reference](docs/USER_GUIDE.md#4-configuration) in the User Guide.

## Project Structure

```
MeetingMinutesTaker/
├── src/meeting_minutes/
│   ├── models.py              # Shared Pydantic data models
│   ├── config.py              # Configuration loading (YAML)
│   ├── encryption.py          # Fernet encryption at rest
│   ├── env.py                 # .env file loading (dotenv)
│   ├── logging.py             # Structured JSON logging
│   ├── pipeline.py            # Pipeline orchestrator (with retry)
│   ├── retention.py           # Data retention policy engine
│   ├── system1/               # Audio capture & transcription
│   │   ├── capture.py         #   AudioCaptureEngine (sounddevice, circular buffer)
│   │   ├── transcribe.py      #   TranscriptionEngine (faster-whisper)
│   │   ├── diarize.py         #   DiarizationEngine (pyannote.audio)
│   │   └── output.py          #   TranscriptJSONWriter
│   ├── system2/               # Minutes generation
│   │   ├── ingest.py          #   TranscriptIngester (validation, speaker mapping)
│   │   ├── router.py          #   PromptRouter (LLM classifier + template selection)
│   │   ├── prompts.py         #   PromptTemplateEngine (Jinja2)
│   │   ├── llm_client.py      #   LLMClient (Anthropic/OpenAI, retry, fallback)
│   │   ├── schema.py          #   StructuredMinutesResponse (tool_use JSON schema)
│   │   ├── parser.py          #   MinutesParser (extract sections, actions, decisions)
│   │   ├── quality.py         #   QualityChecker (coverage, hallucination, length)
│   │   └── output.py          #   MinutesJSONWriter
│   ├── system3/               # Storage & search
│   │   ├── db.py              #   SQLAlchemy ORM + FTS5 virtual table
│   │   ├── storage.py         #   StorageEngine (upsert, person dedup)
│   │   ├── search.py          #   SearchEngine (FTS5, BM25, filter parsing)
│   │   ├── ingest.py          #   MinutesIngester
│   │   └── cli.py             #   Typer CLI (mm command)
│   └── api/                   # FastAPI REST API + WebSocket
│       ├── main.py            #   App factory, CORS, static file serving
│       ├── deps.py            #   Dependency injection
│       ├── schemas.py         #   Pydantic response models
│       ├── ws.py              #   WebSocket (recording status, pipeline progress)
│       └── routes/            #   Route modules (meetings, search, actions, security, retention, etc.)
├── data/
│   └── notes/                 # Live note-taking data (speaker names, notes, instructions)
├── web/                       # Svelte frontend (SvelteKit + Tailwind CSS)
│   └── src/
│       ├── lib/components/    #   14 reusable components
│       ├── lib/stores/        #   Theme + recording state stores
│       └── routes/            #   9 pages (calendar, detail, actions, templates, stats, etc.)
├── templates/                 # Jinja2 meeting-type prompt templates
├── tests/                     # 135 tests (property-based + unit)
├── config/config.yaml         # Default configuration
├── alembic/                   # Database migrations
├── docs/USER_GUIDE.md         # Full setup and usage guide
└── specs/                     # System specifications
```

## Pipeline Modes

| Mode | Behavior |
|------|----------|
| **automatic** | Record stop triggers the full chain: transcribe, generate, index |
| **semi_automatic** | Transcription runs automatically; you manually trigger `mm generate` |
| **manual** | Each step triggered independently via CLI |

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Audio capture | `sounddevice` + BlackHole (macOS) |
| Transcription | `faster-whisper` (Whisper, Metal accelerated) |
| Speaker diarization | `pyannote.audio` |
| LLM | Anthropic Claude (primary), OpenAI (fallback) |
| Database | SQLite + SQLAlchemy |
| Full-text search | SQLite FTS5 with BM25 ranking |
| CLI | `typer` + `rich` |
| Templates | Jinja2 |
| API server | FastAPI + uvicorn |
| Frontend | Svelte + SvelteKit + Tailwind CSS |
| Event bus | `watchdog` (filesystem watcher) |
| Testing | `pytest` + `hypothesis` (property-based) |

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

135 tests covering all 40 correctness properties from the design spec, plus unit tests for every component.

## Documentation

- **[User Guide](docs/USER_GUIDE.md)** — Installation, audio setup, configuration, CLI usage, troubleshooting
- **[Architecture](specs/00-architecture.md)** — System architecture, data flow, deployment topologies
- **[System 1 Spec](specs/01-recording-and-transcription.md)** — Audio capture, transcription, diarization
- **[System 2 Spec](specs/02-minutes-generation.md)** — Minutes generation, templates, LLM integration
- **[System 3 Spec](specs/03-storage-and-search.md)** — Storage, search, API, CLI
- **[Web UI Spec](specs/04-web-ui.md)** — Browser-based GUI (Svelte + FastAPI), all pages, components, API
- **[Design Doc](.kiro/specs/meeting-minutes-taker/design.md)** — Component interfaces, data models, correctness properties
- **[Requirements](.kiro/specs/meeting-minutes-taker/requirements.md)** — Formal acceptance criteria

## License

Private repository. All rights reserved.
