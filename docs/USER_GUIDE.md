# Meeting Minutes Taker — User Guide

This guide walks you through installing the system, setting up audio capture, configuring the pipeline, and using the CLI to record, transcribe, and search your meetings.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Audio Setup (macOS)](#3-audio-setup-macos)
4. [Configuration](#4-configuration)
5. [API Keys](#5-api-keys)
6. [Using the CLI](#6-using-the-cli)
7. [Pipeline Modes](#7-pipeline-modes)
8. [Customizing Templates](#8-customizing-templates)
9. [Managing Your Data](#9-managing-your-data)
10. [Troubleshooting](#10-troubleshooting)

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
cd ~/MeetingMinutesTaker
pip install -e ".[dev]"
```

This installs the `mm` command and all dependencies. Verify it works:

```bash
mm --help
```

### 2.2 Initialize the database

```bash
cd ~/MeetingMinutesTaker
alembic upgrade head
```

This creates `db/meetings.db` with all tables and the full-text search index.

### 2.3 First-run directory setup

On first use, the system creates these directories automatically:

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
5. Right-click the new Multi-Output Device → **Use This Device For Sound Output**

> **Important:** The Built-in Output must be listed first (as the clock/primary device). If BlackHole appears first, uncheck and re-check Built-in Output to reorder it.

> **Note:** macOS does not show a volume slider for Multi-Output Devices. Adjust volume within the meeting app itself or on individual sub-devices in Audio MIDI Setup.

### 3.3 Create an Aggregate Device

An Aggregate Device combines your microphone and BlackHole into one device, so the app can record both streams simultaneously.

1. In **Audio MIDI Setup**, click **+** → **Create Aggregate Device**
2. Check the boxes for:
   - **Built-in Microphone** (or your external mic) — must be first/top
   - **BlackHole 2ch**
3. Enable **Drift Correction** for BlackHole 2ch
4. Rename the Aggregate Device to **MeetingCapture** (or any name you prefer)

### 3.4 Tell the app to use it

Edit `config/config.yaml`:

```yaml
recording:
  audio_device: "MeetingCapture"
```

The app now receives:
- **Channels 1-2**: Your microphone (your voice, room audio)
- **Channels 3-4**: BlackHole (remote participants via Zoom/Teams/Slack/etc.)

### 3.5 Audio signal flow

```
Remote participants (Zoom/Teams/etc.)
        │
        ▼
  System Audio Output
        │
        ├──► Multi-Output Device
        │         ├──► Built-in Output (you hear audio)
        │         └──► BlackHole 2ch (loopback for capture)
        │
        ▼
  Aggregate Device ("MeetingCapture")
        ├──► Built-in Microphone (your voice)
        └──► BlackHole 2ch (remote audio)
        │
        ▼
  Meeting Minutes Taker (captures both)
```

### 3.6 Physical meetings only

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

### 3.7 List available audio devices

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
  audio_device: "MeetingCapture"          # Device name from Audio MIDI Setup, or "auto"
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
    fallback_provider: openai             # Fallback if primary is unavailable
    fallback_model: gpt-4o                # Fallback model
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
  audio_device: "MeetingCapture"

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
2. Accept the user agreement for `pyannote/speaker-diarization-3.1` at its model page
3. Create an access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
4. Set the token:

```bash
export HF_TOKEN="hf_..."
```

If you don't want diarization, set `diarization.enabled: false` in the config and skip this step.

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

### 6.5 Other commands

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

## 7. Pipeline Modes

The three systems (Record, Generate, Store) can be chained in different ways.

### 7.1 Automatic (default)

```yaml
pipeline:
  mode: automatic
```

Recording stop triggers transcription, which triggers minutes generation, which triggers storage. Fully hands-off.

```
mm record stop → Transcribe → Generate Minutes → Store & Index
```

### 7.2 Semi-automatic

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

### 7.3 Manual

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

## 8. Customizing Templates

Meeting minutes are generated using Jinja2 templates in the `templates/` directory. Each meeting type has its own template.

### 8.1 Built-in templates

| File | Meeting Type | When Used |
|------|-------------|-----------|
| `standup.md.j2` | Daily standup | Per-person Done/Today/Blockers |
| `one_on_one.md.j2` | 1:1 meeting | Discussion topics, feedback, action items |
| `customer_meeting.md.j2` | Client/external call | Client requests, commitments, timeline |
| `decision_meeting.md.j2` | Decision meeting | Options, pros/cons, decision, rationale |
| `brainstorm.md.j2` | Brainstorming session | Ideas generated, themes, top ideas |
| `retrospective.md.j2` | Retrospective | Went well, didn't go well, improvements |
| `planning.md.j2` | Sprint/project planning | Goals, tasks, estimates, risks |
| `general.md.j2` | Other / fallback | Summary, discussion points, decisions, actions |

### 8.2 Template variables

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

### 8.3 Creating a custom template

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

## 9. Managing Your Data

### 9.1 Where data is stored

| Data | Location | Retention |
|------|----------|-----------|
| Audio recordings | `data/recordings/*.flac` | Manual deletion |
| Transcripts | `data/transcripts/*.json` | Kept indefinitely |
| Minutes | `data/minutes/*.json`, `*.md` | Kept indefinitely |
| Database | `db/meetings.db` | Kept indefinitely |
| Logs | `logs/*.log` | Manual cleanup |

### 9.2 Deleting a meeting

To completely remove a meeting and all its data (audio, transcript, minutes, database records, search index):

```bash
mm delete <meeting_id>
```

### 9.3 Exporting meetings

```bash
# Export to PDF
mm export <meeting_id> --format pdf

# Export to markdown (already available as .md in data/minutes/)
mm export <meeting_id> --format md
```

### 9.4 Backing up

The entire state of the system is in two places:
- `data/` directory (audio, transcripts, minutes files)
- `db/meetings.db` (SQLite database)

Back up both to preserve everything.

---

## 10. Troubleshooting

### Audio issues

| Problem | Solution |
|---------|----------|
| **No audio captured** | Check that the Multi-Output Device is set as system sound output. Right-click it in Audio MIDI Setup → "Use This Device For Sound Output". |
| **No remote audio** | Make sure Zoom/Teams/Slack is outputting to the system default (which should be the Multi-Output Device). Check the meeting app's audio settings. |
| **Audio glitches or crackling** | Enable Drift Correction on all non-clock devices. Make sure BlackHole 2ch is **not** the primary/clock device (it should be second in the list). |
| **BlackHole not visible** | Restart CoreAudio: `sudo killall -9 coreaudiod` — or reboot. |
| **AirPods cause issues** | AirPods use a lower sample rate. Do not make AirPods the primary/clock device. Use Built-in Output as clock device instead. |
| **"No device found" error** | Run `python -c "import sounddevice; print(sounddevice.query_devices())"` to list available devices. Use the exact name from this list. |

### Transcription issues

| Problem | Solution |
|---------|----------|
| **Slow transcription** | Use a smaller model: set `whisper_model: small` or `whisper_model: base`. On Apple Silicon, Metal acceleration is used automatically. |
| **Poor accuracy** | Use a larger model: `whisper_model: large-v3`. Add domain terms to the custom vocabulary file. |
| **Wrong language detected** | Set the language explicitly: `language: en` (or `nl`, `fr`, `de`, etc.) |
| **Model download stuck** | The first run downloads the Whisper model (~1.5 GB for medium). Ensure you have a stable internet connection. Models are cached in `~/.cache/huggingface/`. |

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
