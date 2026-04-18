# Meeting Minutes Taker

**Stop losing what happened in meetings.** Record any meeting — Zoom, Teams, Slack, Google Meet, or in-person — and get structured, searchable minutes automatically. No cloud recording bots that show up as participants. No monthly SaaS subscriptions. Everything runs locally on your own hardware.

### Why this exists

You spend hours each week in meetings. Key decisions get made, action items get assigned, follow-ups get promised — and most of it evaporates within 24 hours. Generic meeting bots create walls of text nobody reads. This tool produces minutes people actually use: structured by meeting type, with decisions, action items, risks, and follow-ups extracted into dedicated sections.

### What makes it different

- **Private by design** — Audio capture happens at the OS level via system audio loopback. Nothing joins your meeting as a participant. Nothing leaves your machine unless you choose a cloud LLM. Run fully offline with Whisper + Ollama.
- **Meeting-type intelligence** — A standup gets a per-person Done/Today/Blockers breakdown. A decision meeting gets an options matrix with rationale. A customer call gets client requests and commitments. 9 built-in templates, fully customizable.
- **Talk to your meetings** — Ask "What did Jon commit to about lead times since April?" and get a synthesized answer with citations to the specific meetings, powered by local semantic search over your entire meeting history.
- **Speaker identification** — Automatic speaker diarization labels who said what. Enter names once and they propagate through transcripts, minutes, action items, and decisions.
- **Action item tracking** — Every action item extracted across every meeting, filterable by owner, status, and due date. Mark items complete from anywhere in the app.
- **Actually searchable** — Full-text keyword search AND semantic vector search across all transcripts, minutes, and structured data. Find meetings by what was discussed, not just when they happened.
- **Runs on your hardware, optimized for it** — Auto-detects Apple Silicon (Metal), NVIDIA (CUDA), AMD (ROCm), or CPU and configures Whisper and pyannote accordingly. A 13-minute meeting transcribes in under a minute on an M-series Mac or modern GPU.

### Platform support

| Platform | Status | Notes |
|----------|--------|-------|
| **macOS** (12+, Apple Silicon or Intel) | ✅ **First-class** | Turnkey: `install.sh` wires up BlackHole audio loopback, launchd auto-start, Metal acceleration. Most tested path. |
| **Linux** (x86_64 or ARM64) | ✅ Supported | Pipeline works fully. Audio loopback setup is manual (PipeWire/PulseAudio monitor source or `snd-aloop`). CUDA and ROCm auto-detected. |
| **Windows** | ⚙️ Partial | Pipeline, LLM, and search all work. Audio loopback setup (WASAPI) is manual — no install script yet. |

If you're on macOS, everything works out of the box. On Linux/Windows, the processing pipeline runs great; you'll just need to point the app at the right audio device yourself.

---

## How It Works

```
Record Audio ──► Transcribe ──► Generate Minutes ──► Store & Search
  (System 1)      (Whisper)      (Claude / Ollama)     (SQLite + FTS5)
```

**System 1 — Recording & Transcription**: Captures audio from virtual and physical meetings via system audio loopback (BlackHole on macOS), transcribes with a pluggable transcription engine — Faster Whisper (CTranslate2, default) or Whisper.cpp (GGML quantized, lower memory) — including Distil-Whisper models with Metal/CUDA acceleration. Identifies speakers with pyannote.audio (GPU-accelerated via MPS/CUDA), maps speaker labels to user-provided names in first-speaking order, enriches with calendar metadata, and supports live note-taking during recording. Separate `mm rediarize` command can re-run speaker diarization on existing audio without re-transcribing. Hardware auto-detection recommends optimal models for your GPU/RAM.

**System 2 — Minutes Generation**: Auto-detects meeting type using an LLM classifier with meeting type refinement, routes transcripts to meeting-type-specific prompt templates, generates structured minutes via LLM. Supports **four providers**: Anthropic Claude (tool_use for guaranteed JSON), OpenAI, OpenRouter (200+ models), and **Ollama for fully local/offline summarization** (JSON-mode structured generation). Extracts action items and decisions with per-speaker sentiment analysis and meeting effectiveness scoring, and runs quality checks. Supports custom LLM instructions provided during recording.

**System 3 — Storage & Search**: Stores everything in SQLite with full-text search (FTS5) and **semantic vector search** (via `sqlite-vec` + `sentence-transformers`). Provides a CLI for searching, browsing, and managing meetings and action items. **"Chat with your meetings"** feature uses RAG (Retrieval-Augmented Generation) to answer natural-language questions across all your meeting history — e.g., _"Summarize all actions Jon Porter has taken on lead times since April 1st"_. Supports encryption at rest, configurable retention policies, and in-calendar search with filters.

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

## Quick Install

```bash
git clone https://github.com/PietKonijn007/MeetingMinutesTaker.git
cd MeetingMinutesTaker
./install.sh
```

The install script will:
- Check Python 3.11+ and Node.js (installs via Homebrew if missing)
- Install BlackHole 2ch for audio capture
- Create a Python virtual environment and install all dependencies
- Build the web frontend
- Initialize the database
- Prompt for API keys (Anthropic, HuggingFace)
- Set up auto-start on login (macOS Launch Agent)

After installation:
```bash
mm service start              # Start the server
open http://localhost:8080     # Open the web UI
```

### Service Management

```bash
mm service install    # Install auto-start (runs on login)
mm service start      # Start now
mm service stop       # Stop the server
mm service status     # Check if running
mm service logs       # View server logs
mm service logs -f    # Follow logs in real-time
mm service uninstall  # Remove auto-start
```

## Quick Start (Manual)

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
export ANTHROPIC_API_KEY="sk-ant-..."    # Required for minutes generation (Anthropic provider)
export HF_TOKEN="hf_..."                 # Required for speaker diarization
export OPENAI_API_KEY="sk-..."           # Optional (OpenAI provider or fallback)
export OPENROUTER_API_KEY="sk-or-..."    # Optional (OpenRouter provider — access 200+ models)
# No API key needed for Ollama — it runs locally
```

Or create a `.env` file at the project root (takes priority over environment variables):

```
ANTHROPIC_API_KEY=sk-ant-...
HF_TOKEN=hf_...
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
OLLAMA_BASE_URL=http://localhost:11434   # Optional, defaults to localhost
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
| `mm reprocess <id>` | Re-run generation + ingestion (skips transcription/diarization) |
| `mm rediarize <id>` | Re-run speaker diarization on existing audio (skips transcription) |
| `mm embed` | Generate semantic search embeddings for all meetings (run once to backfill) |
| `mm embed <id>` | Embed a single meeting |
| `mm delete <id>` | Delete meeting and all associated data |
| `mm cleanup` | Run retention policy cleanup (delete expired data) |
| `mm generate-key` | Generate a new encryption key for at-rest encryption |
| `mm serve` | Start the web UI + API server (supports `--host`, `--port`, `--auto-port/--no-auto-port`) |
| `mm upgrade` | Pull latest code from main and rebuild (supports `--branch` override) |
| `mm service install` | Install macOS Launch Agent for auto-start on login |
| `mm service uninstall` | Remove the macOS Launch Agent |
| `mm service start` | Start the service now |
| `mm service stop` | Stop the service |
| `mm service status` | Show service status and API health |
| `mm service logs` | Show server logs (supports `--follow`, `--lines`) |

## Web UI

A browser-based interface built with Svelte + Tailwind CSS at `localhost:8080` with calendar view, action items, decisions, people, stats, recording controls, template manager, and settings.

```bash
# Start the server
mm serve

# Open in your browser
open http://localhost:8080
```

**Pages**: Meetings (calendar view with day list + inline detail + search with filters), **Chat** (talk to your meetings — ask natural-language questions across all meeting history with citations), Meeting Detail, Action Items, Decisions, People, Stats (charts), Record (live waveform + concurrent pipeline status + live note-taking), Templates (view/edit/create prompt templates), Settings (LLM provider/model selection with custom model support, Performance & Hardware, Security, Retention, and CORS config).

**Features**: Dark mode, full-text search with `Cmd+K`, in-calendar search with type filter chips, keyboard navigation, responsive layout, meeting type color coding, WebSocket-based real-time updates, concurrent pipeline processing (record a new meeting while the previous one processes in background), auto-detect capture device, auto-save recovery every 5 minutes during recording, live note-taking during recording (speaker names, notes, custom LLM instructions), structured card-based minutes view with collapsible discussion topics, color-coded transcript per-speaker with inline "Name speakers" editor, people management (edit / delete / merge duplicate entities with automatic historical attribution updates), Performance & Hardware settings (Apple Silicon MPS toggle), encryption at rest, retention policies with automatic cleanup.

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
  primary_engine: whisper            # whisper | whisper-cpp
  whisper_model: medium              # tiny | base | small | medium | large-v3

diarization:
  enabled: true

generation:
  llm:
    primary_provider: anthropic       # anthropic | openai | openrouter | ollama
    model: claude-sonnet-4-6          # Model ID (provider-specific)
    temperature: 0.2
    ollama:
      base_url: http://localhost:11434  # Ollama server URL
      timeout_seconds: 300              # Local models can be slower

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
│   ├── hardware.py            # GPU/RAM detection and model recommendations
│   ├── embeddings.py          # Semantic search: chunk + embed + sqlite-vec index
│   ├── chat.py                # RAG chat engine: query parsing + hybrid retrieval + LLM
│   ├── system1/               # Audio capture & transcription
│   │   ├── capture.py         #   AudioCaptureEngine (sounddevice, circular buffer)
│   │   ├── transcribe.py      #   Transcription engine factory (faster-whisper, whisper.cpp)
│   │   ├── diarize.py         #   DiarizationEngine (pyannote.audio)
│   │   └── output.py          #   TranscriptJSONWriter
│   ├── system2/               # Minutes generation
│   │   ├── ingest.py          #   TranscriptIngester (validation, speaker mapping)
│   │   ├── router.py          #   PromptRouter (LLM classifier + template selection)
│   │   ├── prompts.py         #   PromptTemplateEngine (Jinja2)
│   │   ├── llm_client.py      #   LLMClient (Anthropic/OpenAI/OpenRouter/Ollama, retry, fallback)
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
| Transcription | `faster-whisper` (default, Metal/CUDA), `whisper.cpp` (GGML, lower memory) |
| Speaker diarization | `pyannote.audio` |
| LLM | Anthropic Claude (primary), OpenRouter (200+ models), OpenAI, Ollama (local) |
| Database | SQLite + SQLAlchemy |
| Full-text search | SQLite FTS5 with BM25 ranking |
| Semantic search | `sentence-transformers` (bge-small) + `sqlite-vec` |
| Chat/RAG | Hybrid retrieval (FTS5 + vector) → LLM synthesis with citations |
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
