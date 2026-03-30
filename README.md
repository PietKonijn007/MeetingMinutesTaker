# Meeting Minutes Taker

A local-first system that records your meetings, generates intelligent meeting minutes using LLMs, and makes them searchable. Works with Zoom, Teams, Slack, Google Meet, and in-person meetings.

## How It Works

```
Record Audio ──► Transcribe ──► Generate Minutes ──► Store & Search
  (System 1)      (Whisper)      (Claude / GPT)       (SQLite + FTS5)
```

**System 1 — Recording & Transcription**: Captures audio from virtual and physical meetings via system audio loopback (BlackHole on macOS), transcribes with Whisper, identifies speakers with pyannote.audio, and enriches with calendar metadata.

**System 2 — Minutes Generation**: Routes transcripts to meeting-type-specific prompt templates, generates structured minutes via LLM (Anthropic Claude by default), extracts action items and decisions, and runs quality checks.

**System 3 — Storage & Search**: Stores everything in SQLite with full-text search (FTS5), provides a CLI for searching, browsing, and managing meetings and action items.

## Supported Meeting Types

| Type | Template Focus |
|------|---------------|
| `standup` | Per-person Done / Today / Blockers |
| `one_on_one` | Discussion topics, feedback, career development |
| `customer_meeting` | Client requests, commitments, timeline |
| `decision_meeting` | Options, pros/cons, decision, rationale |
| `brainstorm` | Ideas generated, themes, top ideas |
| `retrospective` | Went well, didn't go well, improvements |
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
alembic upgrade head
```

### Set API keys

```bash
export ANTHROPIC_API_KEY="sk-ant-..."    # Required for minutes generation
export HF_TOKEN="hf_..."                 # Required for speaker diarization
export OPENAI_API_KEY="sk-..."           # Optional fallback
```

### Configure audio

Set up BlackHole and create an Aggregate Device named `MeetingCapture` (see [User Guide](docs/USER_GUIDE.md#3-audio-setup-macos) for step-by-step instructions), then:

```yaml
# config/config.yaml
recording:
  audio_device: "MeetingCapture"
```

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

## Configuration

Configuration lives at `config/config.yaml` (fallback: `~/.meeting-minutes/config.yaml`).

Key settings:

```yaml
pipeline:
  mode: automatic              # automatic | semi_automatic | manual

recording:
  audio_device: "MeetingCapture"
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
```

See the full [configuration reference](docs/USER_GUIDE.md#4-configuration) in the User Guide.

## Project Structure

```
MeetingMinutesTaker/
├── src/meeting_minutes/
│   ├── models.py              # Shared Pydantic data models
│   ├── config.py              # Configuration loading (YAML)
│   ├── logging.py             # Structured JSON logging
│   ├── pipeline.py            # Pipeline orchestrator
│   ├── system1/               # Audio capture & transcription
│   │   ├── capture.py         #   AudioCaptureEngine (sounddevice, circular buffer)
│   │   ├── transcribe.py      #   TranscriptionEngine (faster-whisper)
│   │   ├── diarize.py         #   DiarizationEngine (pyannote.audio)
│   │   └── output.py          #   TranscriptJSONWriter
│   ├── system2/               # Minutes generation
│   │   ├── ingest.py          #   TranscriptIngester (validation, speaker mapping)
│   │   ├── router.py          #   PromptRouter (type-based template selection)
│   │   ├── prompts.py         #   PromptTemplateEngine (Jinja2)
│   │   ├── llm_client.py      #   LLMClient (Anthropic/OpenAI, retry, fallback)
│   │   ├── parser.py          #   MinutesParser (extract sections, actions, decisions)
│   │   ├── quality.py         #   QualityChecker (coverage, hallucination, length)
│   │   └── output.py          #   MinutesJSONWriter
│   └── system3/               # Storage & search
│       ├── db.py              #   SQLAlchemy ORM + FTS5 virtual table
│       ├── storage.py         #   StorageEngine (upsert, person dedup)
│       ├── search.py          #   SearchEngine (FTS5, BM25, filter parsing)
│       ├── ingest.py          #   MinutesIngester
│       └── cli.py             #   Typer CLI (mm command)
├── templates/                 # Jinja2 meeting-type prompt templates
├── tests/                     # 115 tests (property-based + unit)
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
| Event bus | `watchdog` (filesystem watcher) |
| Testing | `pytest` + `hypothesis` (property-based) |

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

115 tests covering all 37 correctness properties from the design spec, plus unit tests for every component.

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
