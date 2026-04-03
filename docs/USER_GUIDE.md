# Meeting Minutes Taker — User Guide

This guide walks you through installing the system, setting up audio capture, configuring the pipeline, and using both the CLI and web UI to record, transcribe, and search your meetings.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Audio Setup (macOS)](#3-audio-setup-macos)
4. [Configuration](#4-configuration)
5. [API Keys](#5-api-keys)
6. [Using the CLI](#6-using-the-cli)
7. [Using the Web UI](#7-using-the-web-ui)
8. [Pipeline Modes](#8-pipeline-modes)
9. [Customizing Templates](#9-customizing-templates)
10. [Template Manager (Web UI)](#10-template-manager-web-ui)
11. [Managing Your Data](#11-managing-your-data)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

| Requirement | Details |
|-------------|---------|
| **Python** | 3.11 or newer |
| **OS** | macOS 12+ (recommended: macOS 14+ on Apple Silicon) |
| **RAM** | 8 GB minimum, 16 GB recommended |
| **Disk** | ~1 GB per hour of recorded audio |
| **Whisper model** | Downloaded on first use (~1.5 GB for the `medium` model) |

For speaker diarization, you also need a free HuggingFace account with an access token (the `pyannote.audio` models are gated).

---

## 2. Installation

### 2.1 Clone and install

```bash
git clone https://github.com/PietKonijn007/MeetingMinutesTaker.git
cd MeetingMinutesTaker
pip install -e ".[dev]"
```

This installs the `mm` command and all dependencies. Verify it works:

```bash
mm --help
```

### 2.2 Initialize the project

```bash
mm init
```

This creates the database (`db/meetings.db`), data directories (`data/recordings/`, `data/transcripts/`, `data/minutes/`), and the logs directory in one step. It also runs any pending Alembic database migrations.

### 2.3 Install the web UI (optional)

If you want to use the browser-based interface:

```bash
cd web
npm install
cd ..
```

### 2.4 Directory layout

After initialization, the project has this structure:

```
~/MeetingMinutesTaker/
├── data/
│   ├── recordings/      # Audio files (FLAC)
│   ├── transcripts/     # Transcript JSON files
│   ├── minutes/         # Generated minutes (JSON + Markdown)
│   └── exports/         # PDF/HTML exports
├── db/
│   └── meetings.db      # SQLite database
└── logs/                # Structured JSON logs
```

---

## 3. Audio Setup (macOS)

To capture both sides of a virtual meeting (your voice **and** the remote participants), you need a virtual audio loopback. This section walks you through it using BlackHole, a free open-source driver.

### 3.1 Install BlackHole 2ch

**Option A — Homebrew (recommended):**

```bash
brew install --cask blackhole-2ch
```

**Option B — Manual download:**

Download the installer from [existentialaudio.com](https://existentialaudio.com/blackhole/) or [GitHub](https://github.com/ExistentialAudio/BlackHole). Run the `.pkg` installer and restart when prompted.

**Verify installation:** Open **Audio MIDI Setup** (press `Cmd + Space`, type `Audio MIDI Setup`) and check that "BlackHole 2ch" appears in the device list.

### 3.2 Create a Multi-Output Device

A Multi-Output Device sends audio to two places at once — your speakers (so you hear the meeting) and BlackHole (so the app can capture it).

1. Open **Audio MIDI Setup**
2. Click the **+** button in the bottom-left → **Create Multi-Output Device**
3. Check the boxes for:
   - **Built-in Output** (or MacBook Pro Speakers) — this must be first/top
   - **BlackHole 2ch**
4. Enable **Drift Correction** for BlackHole 2ch (leave it off for the top device)
5. Rename the Multi-Output Device to **Meeting Output** (with the emoji prefix: `🔊 Meeting Output`)
6. Right-click **Meeting Output** → **Use This Device For Sound Output**

> **Important:** The Built-in Output must be listed first (as the clock/primary device). If BlackHole appears first, uncheck and re-check Built-in Output to reorder it.

> **Note:** macOS does not show a volume slider for Multi-Output Devices. Adjust volume within the meeting app itself or on individual sub-devices in Audio MIDI Setup.

### 3.3 Create an Aggregate Device

An Aggregate Device combines your microphone and BlackHole into one device, so the app can record both streams simultaneously.

1. In **Audio MIDI Setup**, click **+** → **Create Aggregate Device**
2. Check the boxes for:
   - **Built-in Microphone** (or your external mic) — must be first/top
   - **BlackHole 2ch**
3. Enable **Drift Correction** for BlackHole 2ch
4. Rename the Aggregate Device to **Meeting Capture** (with the emoji prefix: `🎙 Meeting Capture`)

### 3.4 Meeting app audio settings

In **Zoom, Google Meet, Teams, Slack**, etc.: leave audio settings at their defaults. The system-level Multi-Output Device routes meeting audio to both your speakers and BlackHole automatically — no per-app configuration needed.

### 3.5 Tell the Meeting Minutes app to use it

In the Meeting Minutes web UI (Settings page) or in `config/config.yaml`, select **Meeting Capture** as the input device:

```yaml
recording:
  audio_device: "Meeting Capture"
```

The app now receives:
- **Channels 1-2**: Your microphone (your voice, room audio)
- **Channels 3-4**: BlackHole (remote participants via Zoom/Teams/Slack/etc.)

**Summary of device roles:**
- **System output** → set to `🔊 Meeting Output` (you hear audio + BlackHole captures it)
- **Meeting Minutes input** → set to `🎙 Meeting Capture` (mic + BlackHole combined)

### 3.6 Audio signal flow

```
Remote participants (Zoom/Teams/etc.)
        │
        ▼
  System Audio Output
        │
        ├──► 🔊 Meeting Output (Multi-Output Device)
        │         ├──► Built-in Output (you hear audio)
        │         └──► BlackHole 2ch (loopback for capture)
        │
        ▼
  🎙 Meeting Capture (Aggregate Device)
        ├──► Built-in Microphone (your voice)
        └──► BlackHole 2ch (remote audio)
        │
        ▼
  Meeting Minutes Taker (captures both)
```

### 3.7 Physical meetings only

If you're only recording in-person meetings (no virtual component), skip the BlackHole setup. Just set your mic directly:

```yaml
recording:
  audio_device: "MacBook Pro Microphone"
```

Or use `"auto"` to use the system default input device:

```yaml
recording:
  audio_device: "auto"
```

### 3.8 List available audio devices

To see which audio devices are available on your system:

```bash
python -c "import sounddevice; print(sounddevice.query_devices())"
```

Use the exact device name from this list in your config.

---

## 4. Configuration

Configuration lives at `config/config.yaml`. The app also checks `~/.meeting-minutes/config.yaml` as a fallback. If neither exists, built-in defaults are used.

### 4.1 Full configuration reference

```yaml
# ─── General ────────────────────────────────────────────
data_dir: ~/MeetingMinutesTaker/data     # Where audio, transcripts, minutes are stored
log_level: INFO                           # DEBUG | INFO | WARNING | ERROR

# ─── Pipeline ──────────────────────────────────────────
pipeline:
  mode: automatic                         # automatic | semi_automatic | manual
                                          # See "Pipeline Modes" section below

# ─── Recording (System 1) ──────────────────────────────
recording:
  audio_device: "Meeting Capture"          # Device name from Audio MIDI Setup, or "auto"
  sample_rate: 16000                      # 16000 Hz is optimal for speech recognition
  format: flac                            # Audio format: flac (lossless)
  auto_stop_silence_minutes: 5            # Stop recording after N minutes of silence

# ─── Transcription (System 1) ──────────────────────────
transcription:
  primary_engine: whisper                 # whisper (local) — only option for MVP
  whisper_model: medium                   # tiny | base | small | medium | large-v3
                                          #   tiny:  ~1 GB RAM, fast, lower accuracy
                                          #   base:  ~1 GB RAM, good for short meetings
                                          #   small: ~2 GB RAM, good balance
                                          #   medium: ~5 GB RAM, recommended default
                                          #   large-v3: ~10 GB RAM, best accuracy
  language: auto                          # "auto" to detect, or ISO code ("en", "nl", "fr")
  custom_vocabulary: null                 # Path to a text file with custom words
                                          # (one per line: company names, jargon, acronyms)

# ─── Speaker Diarization (System 1) ────────────────────
diarization:
  enabled: true                           # Set to false to skip speaker identification
  engine: pyannote                        # Requires HuggingFace token (see API Keys)

# ─── Minutes Generation (System 2) ─────────────────────
generation:
  templates_dir: templates                # Directory containing .md.j2 template files
  llm:
    primary_provider: anthropic           # anthropic | openai
    model: claude-sonnet-4-6-20250514       # Model for minutes generation
    fallback_provider: null                # Fallback provider (null = disabled, or "openai")
    fallback_model: gpt-4o                # Fallback model (used when fallback_provider is set)
    temperature: 0.2                      # Low = more factual, less creative
    max_output_tokens: 4096               # Max length of generated minutes
    retry_attempts: 3                     # Retries on API failure
    timeout_seconds: 120                  # API call timeout

# ─── Storage (System 3) ────────────────────────────────
storage:
  database: sqlite                        # Only option for now
  sqlite_path: db/meetings.db             # Path to SQLite database file
```

### 4.2 Minimal configuration

If you just want to get started, create `config/config.yaml` with only what you need to change:

```yaml
recording:
  audio_device: "Meeting Capture"

generation:
  llm:
    primary_provider: anthropic
    model: claude-sonnet-4-6-20250514
```

Everything else uses sensible defaults.

### 4.3 Custom vocabulary

Create a text file with domain-specific terms the transcriber should recognize:

```
# config/vocabulary.txt
Kubernetes
NextJS
PostgreSQL
MyCompanyName
JIRA
OKR
```

Then point the config at it:

```yaml
transcription:
  custom_vocabulary: config/vocabulary.txt
```

---

## 5. API Keys

### 5.1 Anthropic (required for minutes generation)

Get your API key from [console.anthropic.com](https://console.anthropic.com/).

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Add this to your shell profile (`~/.zshrc` or `~/.bashrc`) to persist it.

### 5.2 OpenAI (optional fallback)

If you configure an OpenAI fallback:

```bash
export OPENAI_API_KEY="sk-..."
```

### 5.3 HuggingFace (required for speaker diarization)

The `pyannote.audio` models are gated. You need to:

1. Create a free account at [huggingface.co](https://huggingface.co/)
2. Accept the license/user agreement on **all three** model pages:
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
   - [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
3. Create an access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
4. Set the token:

```bash
export HF_TOKEN="hf_..."
```

If you don't want diarization, set `diarization.enabled: false` in the config and skip this step.

### 5.4 Using a `.env` file

Instead of setting environment variables in your shell, you can create a `.env` file at the project root. Values in `.env` take priority over variables set in the shell environment.

```
# .env (at the root of MeetingMinutesTaker/)
ANTHROPIC_API_KEY=sk-ant-...
HF_TOKEN=hf_...
OPENAI_API_KEY=sk-...
```

The file is loaded automatically at startup via `env.py`. You do not need to source or export it.

---

## 6. Using the CLI

All commands use the `mm` command.

### 6.1 Recording a meeting

```bash
# Start recording
mm record start
# → Recording started. Meeting ID: a1b2c3d4-...

# ... attend your meeting ...

# Stop recording (triggers transcription + minutes generation in automatic mode)
mm record stop
# → Recording stopped. Meeting ID: a1b2c3d4-...
```

### 6.2 Searching meetings

```bash
# Full-text search
mm search "database migration"

# Filter by meeting type
mm search "budget" --type decision_meeting

# Filter by date
mm search "sprint" --after 2026-03-01 --before 2026-03-31

# Limit results
mm search "onboarding" --limit 5
```

### 6.3 Browsing meetings

```bash
# List recent meetings
mm list

# List meetings with a specific person
mm list --person alice@company.com

# Show full details of a meeting
mm show <meeting_id>

# Show the raw transcript
mm transcript <meeting_id>
```

### 6.4 Managing action items

```bash
# List all open action items
mm actions

# Filter by owner
mm actions --owner bob@company.com

# Show overdue items
mm actions --overdue

# Mark an action item as done
mm actions complete <action_id>
```

### 6.5 Starting the web UI

```bash
# Start the API server + web UI
mm serve

# Custom host/port
mm serve --host 0.0.0.0 --port 9090
```

Then open [http://localhost:8080](http://localhost:8080) in your browser. See section 7 for details.

### 6.6 Other commands

```bash
# Generate minutes from an existing transcript (manual trigger)
mm generate <meeting_id>

# Re-run the entire pipeline for a meeting
mm reprocess <meeting_id>

# Delete a meeting and all its data
mm delete <meeting_id>

# Delete without confirmation prompt
mm delete <meeting_id> --yes
```

---

## 7. Using the Web UI

The web UI is a browser-based interface for browsing, searching, and managing your meetings. It runs at `localhost:8080` and provides everything the CLI does, plus visual features like charts, audio playback, and a live recording interface.

### 7.1 Starting the server

```bash
mm serve
```

Open [http://localhost:8080](http://localhost:8080) in your browser. The API documentation is available at [http://localhost:8080/docs](http://localhost:8080/docs) (auto-generated by FastAPI).

### 7.2 Pages overview

| Page | URL | What it shows |
|------|-----|---------------|
| **Meetings** | `/` | Calendar view with day list and inline meeting detail. Filter by type, search. |
| **Meeting Detail** | `/meeting/:id` | Full minutes, transcript with audio player, action items, decisions, tags. |
| **Action Items** | `/actions` | All action items across meetings. Filter by owner, status, overdue. Check items off. |
| **Decisions** | `/decisions` | Chronological log of all decisions, grouped by date. |
| **People** | `/people` | Directory of everyone who has appeared in meetings, with meeting counts and action items. |
| **Stats** | `/stats` | Charts: meetings over time, distribution by type, action item velocity, time in meetings. |
| **Record** | `/record` | Start/stop recording with live timer, audio levels, auto-detected device. Shows concurrent pipeline job status. |
| **Templates** | `/templates` | View, edit, and create meeting prompt templates. Built-in templates are protected from deletion. |
| **Settings** | `/settings` | Visual configuration editor for all settings (audio device, Whisper model, LLM, pipeline mode). |

### 7.3 Navigation

- **Sidebar**: Always visible on desktop (collapses on mobile). Shows all pages (including Templates) plus a recording status indicator.
- **Search**: Global search bar in the top bar. Press `Cmd+K` (or `Ctrl+K`) from anywhere to focus it.
- **Dark mode**: Toggle via the sun/moon icon in the top-right corner. Follows your system preference on first visit.

### 7.4 Meetings page (Calendar View)

The default landing page is a calendar-first view with two panels:

- **Left panel**: A month calendar with colored dots on days that have meetings. Below the calendar, a list of meetings for the selected day. Click any day to see its meetings.
- **Right panel**: Meeting detail rendered inline — clicking a meeting in the day list shows its full minutes, transcript, and actions without navigating away from the page.

Filter using the controls at the top:
- **Search**: Full-text search across all transcripts and minutes.
- **Type**: Filter by meeting type (standup, decision, etc.) — multi-select.

### 7.5 Meeting detail page

The richest page. Shows everything about one meeting.

**Header**: Title, metadata pills (type, duration, date, attendee count), attendee list, tags.

**Four tabs**:

| Tab | Content |
|-----|---------|
| **Minutes** | Rendered markdown of the generated minutes. Action item checkboxes are interactive — click to mark done. |
| **Transcript** | Full transcript with speaker labels and timestamps. If an audio file exists, an audio player appears at the top. Click any timestamp to jump to that point in the audio. The currently playing segment highlights automatically. |
| **Actions** | Action items from this meeting with checkboxes to toggle status. |
| **Decisions** | Decisions made, with who made them. |

**Actions at the bottom**:
- **Regenerate**: Re-run LLM generation with the current transcript.
- **Export**: Download as Markdown or PDF.
- **Delete**: Remove the meeting and all its data (with confirmation).

### 7.6 Action items page

Shows all action items across all meetings. Items are grouped: open items first, completed items collapsed at the bottom.

- Click the checkbox to mark an item done (updates instantly).
- Overdue items are highlighted with a warning indicator.
- Filter by owner (dropdown of all people) or status (open/done/all).
- Each item links to the meeting it came from.

### 7.7 Stats page

Dashboard with four summary cards at the top:
- Total meetings
- Meetings this week
- Open action items
- Average meeting duration

Below, four charts:
- **Meetings over time**: Area chart showing meetings per week (last 12 weeks).
- **By type**: Donut chart of meeting type distribution.
- **Action velocity**: Line chart of action items created vs. completed per week.
- **Top attendees**: Horizontal bar chart of people with the most meetings.

Charts respect dark mode and are interactive (hover for tooltips).

### 7.8 Record page

Start and stop recordings directly from the browser.

- **Idle state**: Large record button, auto-detected audio device (prefers MeetingCapture aggregate devices, skips offline devices like disconnected AirPods) with an "auto-detected" indicator, recent recordings list. You can also select a device manually.
- **Recording state**: Pulsing red indicator, elapsed time counter, audio level bars, Pause and Stop buttons. A small red dot also appears in the top bar so you can see recording status from any page. Audio is auto-saved every 5 minutes as a recovery file in case of crashes.
- **Processing state**: Below the recording controls, a "Processing" section shows per-meeting pipeline status. Each job displays steps — Transcribe, Generate, Index — with checkmarks as they complete. Pipeline jobs run sequentially (queued) to avoid memory thrashing with Whisper.

You can record a new meeting immediately after stopping the previous one — the previous meeting's pipeline continues processing in the background.

Recording and pipeline status updates are delivered in real time via WebSocket push (no polling). Each pipeline job is tracked with step/progress/error and auto-cleans up after 60 seconds.

### 7.9 Settings page

A visual editor for `config/config.yaml`. Organized into sections:

| Section | Settings |
|---------|----------|
| **Recording** | Audio device (dropdown of detected devices), sample rate, auto-stop silence threshold |
| **Transcription** | Whisper model (with size/accuracy descriptions), language |
| **Speaker ID** | Enable/disable diarization |
| **Minutes Generation** | LLM provider, model, temperature, max tokens |
| **Pipeline** | Mode (automatic / semi-automatic / manual) |
| **Storage** | Database path, data directory |
| **Appearance** | Dark mode toggle |

Changes are saved when you click the Save button at the bottom.

### 7.10 Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+K` / `Ctrl+K` | Focus global search |
| `Cmd+R` / `Ctrl+R` | Toggle recording start/stop |
| `Esc` | Close modal / clear search / go back |
| `J` / `K` | Navigate down/up in meeting list |
| `Enter` | Open selected meeting |
| `Space` | Play/pause audio (on transcript tab) |
| `←` / `→` | Seek audio +/- 5 seconds |

### 7.11 Development mode

For frontend development with hot module replacement:

```bash
# Terminal 1: Start the API backend
mm serve

# Terminal 2: Start the Svelte dev server with hot reload
cd web
npm run dev
```

The Svelte dev server runs at `localhost:3000` and proxies all `/api` and `/ws` requests to the FastAPI backend at `localhost:8080`. Edit Svelte components and see changes instantly in the browser.

For production, build the frontend and serve everything from FastAPI:

```bash
cd web && npm run build && cd ..
mm serve
# → API + UI both served at localhost:8080
```

---

## 8. Pipeline Modes

The three systems (Record, Generate, Store) can be chained in different ways.

### 8.1 Automatic (default)

```yaml
pipeline:
  mode: automatic
```

Recording stop triggers transcription, which triggers minutes generation, which triggers storage. Fully hands-off.

```
mm record stop → Transcribe → Generate Minutes → Store & Index
```

### 8.2 Semi-automatic

```yaml
pipeline:
  mode: semi_automatic
```

Recording and transcription happen automatically. You review the transcript, then manually trigger minutes generation.

```
mm record stop → Transcribe         (automatic)
mm generate <meeting_id>            (manual trigger)
  → Generate Minutes → Store & Index
```

This is useful when you want to verify the transcript before generating minutes.

### 8.3 Manual

```yaml
pipeline:
  mode: manual
```

Each step is triggered independently. Useful for debugging or reprocessing individual stages.

```
mm record start / mm record stop     # Record audio
mm transcribe <meeting_id>           # Transcribe
mm generate <meeting_id>             # Generate minutes
mm ingest <meeting_id>               # Store in database
```

---

## 9. Customizing Templates

Meeting minutes are generated using Jinja2 templates in the `templates/` directory. Each meeting type has its own template.

### 9.1 Built-in templates

| File | Meeting Type | When Used |
|------|-------------|-----------|
| `standup.md.j2` | Daily standup | Per-person Done/Today/Blockers |
| `one_on_one.md.j2` | 1:1 meeting | Mood check-in, accomplishments, objectives progress, blockers, feedback (SBI), career development, coaching notes, engagement signals, action items |
| `customer_meeting.md.j2` | Client/external call | Client requests, commitments, timeline |
| `decision_meeting.md.j2` | Decision meeting | Options, pros/cons, decision, rationale |
| `brainstorm.md.j2` | Brainstorming session | Ideas generated, themes, top ideas |
| `retrospective.md.j2` | Retrospective | Went well, didn't go well, improvements |
| `team_meeting.md.j2` | Team meeting | Prior action items, decisions, financial review, blockers, strategic updates, action items |
| `planning.md.j2` | Sprint/project planning | Goals, tasks, estimates, risks |
| `general.md.j2` | Other / fallback | Summary, discussion points, decisions, actions |

### 9.2 Template variables

Every template receives these variables:

| Variable | Type | Description |
|----------|------|-------------|
| `date` | `str` | Meeting date (`YYYY-MM-DD`) |
| `title` | `str` | Meeting title (from calendar or filename) |
| `duration` | `str` | Meeting duration (`HH:MM:SS`) |
| `attendees` | `list[str]` | List of attendee names |
| `organizer` | `str` | Organizer name |
| `meeting_type` | `str` | Classified meeting type |
| `transcript_text` | `str` | Full transcript with speaker labels |

### 9.3 Creating a custom template

1. Create a new `.md.j2` file in `templates/`:

```markdown
{# templates/board_meeting.md.j2 #}
You are an expert meeting minutes assistant specializing in board meetings.
Extract formal decisions, voting results, and compliance-relevant items.

---
# Board Meeting Minutes — {{ date }}

**Attendees:** {{ attendees | join(', ') }}
**Duration:** {{ duration }}

## Transcript
{{ transcript_text }}

---

Please produce board meeting minutes with these sections:

## Summary
[Executive summary]

## Agenda Items
[List agenda items discussed]

## Motions & Votes
[Any formal motions, who proposed, voting results]

## Decisions
[All decisions made, with rationale]

## Action Items
- [ ] [Description] — Owner: [Name] (Due: [Date if mentioned])

## Key Topics
[List main topics]
```

2. To use it, override the meeting type when generating:

```bash
mm generate <meeting_id> --type board_meeting
```

---

## 10. Template Manager (Web UI)

The Template Manager lets you view, edit, and create meeting prompt templates directly from the browser at `/templates`.

### 10.1 Viewing and editing templates

All `.md.j2` template files from the `templates/` directory appear in the Template Manager. Click any template to view its contents in an editor. Built-in templates (standup, one_on_one, customer_meeting, etc.) are protected from deletion but can be edited.

### 10.2 Creating custom meeting types

To create a new meeting type:

1. Go to the Templates page and click **New Template**.
2. Enter a name (e.g., `board_meeting`). This creates `templates/board_meeting.md.j2`.
3. Write your prompt template using Jinja2 syntax (see section 9 for template variables).
4. Save. The new type is immediately available as a valid meeting type throughout the system.

Any `.md.j2` file you add to the `templates/` directory — whether via the Template Manager or by creating the file directly — becomes a valid meeting type.

### 10.3 LLM-Based Meeting Type Classification

The system uses an LLM classifier (Claude Haiku) to automatically detect meeting types. When the initial keyword-based classifier produces a confidence score below 0.7, the LLM classifier is invoked automatically. It sends the first 4000 characters of the transcript along with metadata (speaker count, calendar title) to the LLM, which returns the meeting type, a confidence score, and reasoning.

The classifier reads actual template descriptions (system prompt and section headings) from your templates directory to make its decision, so custom template types are auto-discovered. The cost is approximately $0.001 per classification. If the Anthropic API is unavailable, the system falls back to keyword matching.

You can always override the detected type manually:

```bash
mm generate <meeting_id> --type team_meeting
```

### 10.5 Updated built-in templates

The `one_on_one`, `customer_meeting`, and `team_meeting` templates include enriched sections:

- **one_on_one**: Mood/Energy Check-in, Accomplishments & Wins (dated), Progress Against Objectives, Blockers (4 types with handling status + support needed), Feedback Given (SBI format), Feedback Received (upward), Service Feedback, Customer Feedback, Team & Org Observations, Career Development, Coaching Notes, Engagement Signals (positive + concerning), Action Items (split into employee vs manager commitments)
- **team_meeting**: Prior Action Items Review, Decisions (with rationale), Financial Review (cloud spend, optimization, P&L), Blockers (4 categories + cross-team dependencies), Strategic Updates, Technology Decisions, Service Feedback (AWS/NetApp), Customer Impact, Resource & Capacity, Team Health & Morale, Announcements, Parking Lot, Action Items (split by urgency)
- **Service Feedback** (shared across templates): AWS Services, NetApp Services, Other Services
- **Blockers** (shared across templates): Categorized as Operational, Organizational, Physical/Infrastructure, Roadmap/Strategic
- **Customer meetings** additionally include: Competitive Intelligence, and split Commitments (ours vs. customer's)

---

## 11. Managing Your Data

### 11.1 Where data is stored

| Data | Location | Retention |
|------|----------|-----------|
| Audio recordings | `data/recordings/*.flac` | Manual deletion |
| Transcripts | `data/transcripts/*.json` | Kept indefinitely |
| Minutes | `data/minutes/*.json`, `*.md` | Kept indefinitely |
| Database | `db/meetings.db` | Kept indefinitely |
| Logs | `logs/*.log` | Manual cleanup |

### 11.2 Deleting a meeting

To completely remove a meeting and all its data (audio, transcript, minutes, database records, search index):

```bash
mm delete <meeting_id>
```

### 11.3 Exporting meetings

```bash
# Export to PDF
mm export <meeting_id> --format pdf

# Export to markdown (already available as .md in data/minutes/)
mm export <meeting_id> --format md
```

### 11.4 Backing up

The entire state of the system is in two places:
- `data/` directory (audio, transcripts, minutes files)
- `db/meetings.db` (SQLite database)

Back up both to preserve everything.

---

## 12. Troubleshooting

### Audio issues

| Problem | Solution |
|---------|----------|
| **No audio captured** | Check that "Meeting Output" is set as system sound output. Right-click it in Audio MIDI Setup → "Use This Device For Sound Output". |
| **No remote audio** | Make sure Zoom/Teams/Slack is outputting to the system default (which should be "Meeting Output"). Leave the meeting app's audio settings at their defaults. |
| **Audio glitches or crackling** | Enable Drift Correction on all non-clock devices. Make sure BlackHole 2ch is **not** the primary/clock device (it should be second in the list). |
| **BlackHole not visible** | Restart CoreAudio: `sudo killall -9 coreaudiod` — or reboot. |
| **AirPods cause issues** | AirPods use a lower sample rate. Do not make AirPods the primary/clock device. Use Built-in Output as clock device instead. |
| **"No device found" error** | Run `python -c "import sounddevice; print(sounddevice.query_devices())"` to list available devices. Use the exact name from this list. |
| **PortAudio error -9986 (device unavailable)** | This usually means macOS denied microphone permission. Go to System Settings > Privacy & Security > Microphone and enable access for your terminal app. If the device was plugged in after the app started, the app will re-scan devices automatically via `sd._terminate()` + `sd._initialize()`. |

### Transcription issues

| Problem | Solution |
|---------|----------|
| **Slow transcription** | Use a smaller model: set `whisper_model: small` or `whisper_model: base`. On Apple Silicon, Metal acceleration is used automatically. |
| **Poor accuracy** | Use a larger model: `whisper_model: large-v3`. Add domain terms to the custom vocabulary file. |
| **Wrong language detected** | Set the language explicitly: `language: en` (or `nl`, `fr`, `de`, etc.) |
| **Model download stuck** | The first run downloads the Whisper model (~1.5 GB for medium). Ensure you have a stable internet connection. Models are cached in `~/.cache/huggingface/`. |
| **Out of memory with `large-v3`** | The `large-v3` model requires ~10 GB RAM and a ~3 GB download. On machines with 16 GB RAM, use `medium` instead (the default). You can change the model in the Settings page or `config.yaml`. |
| **NumPy compatibility error with pyannote** | If you see errors about NumPy version incompatibility, pin NumPy: `pip install "numpy<2.0"`. The pyannote.audio library may not yet support NumPy 2.x. |

### Speaker diarization issues

| Problem | Solution |
|---------|----------|
| **Diarization fails with 403 or "gated repo" error** | You must accept the license on **all three** HuggingFace model pages: `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`, and `pyannote/speaker-diarization-community-1`. Missing any one of them causes this error. |
| **Diarization fails with `use_auth_token` error** | The pyannote.audio pipeline now uses `token=` instead of `use_auth_token`. Make sure your `HF_TOKEN` environment variable is set and you are using a recent version of the app. |
| **Native sample rate mismatch** | The app automatically queries the audio device for its native sample rate and uses it for capture. If you see sample rate warnings, check that your audio device supports the detected rate. |

### Minutes generation issues

| Problem | Solution |
|---------|----------|
| **"ANTHROPIC_API_KEY not set"** | Set the env var: `export ANTHROPIC_API_KEY="sk-ant-..."` |
| **API timeout** | Increase `timeout_seconds` in the config. Long meetings may need 180-240 seconds. |
| **Minutes look wrong for meeting type** | Override the type: `mm generate <id> --type standup`. Or edit the template in `templates/`. |
| **Missing action items** | The LLM may miss implicit tasks. Review the transcript and add them manually, or adjust the template to emphasize action item extraction. |

### Database issues

| Problem | Solution |
|---------|----------|
| **"No meetings found"** | Make sure you've run the full pipeline (or at least `mm ingest <id>`). Check that `db/meetings.db` exists. |
| **Search returns nothing** | The FTS index may need rebuilding. Re-ingest: `mm reprocess <meeting_id>`. |
| **Database corruption** | Delete `db/meetings.db` and re-run `alembic upgrade head` to recreate. Then re-ingest your minutes JSON files. |

### Web UI issues

| Problem | Solution |
|---------|----------|
| **`mm serve` fails to start** | Make sure FastAPI and uvicorn are installed: `pip install -e ".[dev]"`. Check that port 8080 isn't already in use. |
| **Blank page at localhost:8080** | The Svelte frontend needs to be built first: `cd web && npm install && npm run build`. In development, use `npm run dev` on port 3000 instead. |
| **API returns 404 for everything** | The database may not be initialized. Run `alembic upgrade head` to create tables. |
| **"CORS error" in browser console** | This only happens if you access the Svelte dev server directly without the proxy. Make sure `vite.config.js` proxies `/api` to `:8080`, or access the API server directly at `:8080`. |
| **Dark mode doesn't persist** | The preference is stored in `localStorage`. Clear your browser storage if it gets stuck, or toggle it again. |
| **Charts not rendering on Stats page** | Chart.js must be installed: `cd web && npm install`. If charts appear but are blank, there may be no meeting data yet — record a few meetings first. |
| **Recording page shows "disconnected"** | The WebSocket connection to the server failed. Make sure `mm serve` is running. The page will auto-reconnect when the server comes back. |
| **Settings don't save** | Check that `config/config.yaml` is writable. The API writes changes to the YAML file on disk. |
