# Meeting Minutes Taker

**Stop losing what happened in meetings.** Record any meeting — Zoom, Teams, Slack, Google Meet, or in-person — and get structured, searchable minutes automatically. No cloud recording bots that show up as participants. No monthly SaaS subscriptions. Everything runs locally on your own hardware.

### Why this exists

You spend hours each week in meetings. Key decisions get made, action items get assigned, follow-ups get promised — and most of it evaporates within 24 hours. Generic meeting bots create walls of text nobody reads. This tool produces minutes people actually use: structured by meeting type, with decisions, action items, risks, and follow-ups extracted into dedicated sections.

### What makes it different

- **Private by design** — Audio capture happens at the OS level via system audio loopback. Nothing joins your meeting as a participant. Nothing leaves your machine unless you choose a cloud LLM. Run fully offline with Whisper + Ollama.
- **Meeting-type intelligence** — A standup gets a per-person Done/Today/Blockers breakdown. A decision meeting gets an options matrix with rationale. A customer call gets client requests and commitments. An incident review gets a blameless timeline and contributing factors. 18 built-in templates covering the real exec calendar (board, leadership, vendor, architecture review, interview debrief, three 1:1 variants, and more) — fully customizable.
- **Talk to your meetings** — Ask "What did Jon commit to about lead times since April?" and get a synthesized answer with citations to the specific meetings, powered by local semantic search over your entire meeting history.
- **Speaker identification** — Automatic speaker diarization labels who said what. Enter names once and they propagate through transcripts, minutes, action items, and decisions.
- **Action items you actually own** — Extracted action items land as **proposals** on the meeting page. You Accept (turns into a tracked action), Edit (fix wording / owner / due date before accepting), or Reject the LLM's suggestions. Only confirmed actions reach the global tracker, the prior-action carryover that feeds the next meeting's prompt, the Obsidian export, and the DOCX export — so the tracker stays a list of things you actually agreed to, not a stream of LLM guesses.
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

**System 2 — Minutes Generation**: Auto-detects meeting type using an LLM classifier (Claude Haiku, with a calendar-title + content + attendee-count keyword fallback), routes transcripts to meeting-type-specific prompt templates, generates structured minutes via LLM. Supports **four providers**: Anthropic Claude (tool_use for guaranteed JSON), OpenAI, OpenRouter (200+ models), and **Ollama for fully local/offline summarization** (JSON-mode structured generation). Produces four layers of output per meeting: a ~100-word executive **TL;DR**, a short `summary`, a `detailed_notes` narrative (length controlled by `generation.length_mode` = `concise` / `standard` / `verbose`), and structured lists for decisions, action items, risks, open questions, follow-ups, and a ready-to-send email draft. Action items are emitted as **proposals** that you confirm before they enter the tracker (see "Action item review workflow" below). Extracts sentiment, meeting effectiveness, a confidentiality classification, and carries forward only **confirmed** open action items from prior meetings — automatically closing them in the DB when they're acknowledged as done in a later meeting. Vendor-feedback sub-sections are driven by a configurable `generation.vendors` list (default `[AWS, NetApp]`). Supports custom LLM instructions provided during recording.

**System 3 — Storage & Search**: Stores everything in SQLite with full-text search (FTS5) and **semantic vector search** (via `sqlite-vec` + `sentence-transformers`). Provides a CLI for searching, browsing, and managing meetings and action items. **"Chat with your meetings"** feature uses RAG (Retrieval-Augmented Generation) to answer natural-language questions across all your meeting history — e.g., _"Summarize all actions Jon Porter has taken on lead times since April 1st"_. Supports encryption at rest, configurable retention policies, and in-calendar search with filters.

## Supported Meeting Types

Every template emits a shared baseline — **TL;DR**, decisions, action items, risks, open questions, confidentiality classification, and a follow-up email draft — plus the type-specific sections below. Empty sections are omitted (no "Not discussed" filler).

### Team & cadence
| Type | Template Focus |
|------|---------------|
| `standup` | Per-person Done / Today / Blockers |
| `team_meeting` | Decisions, financial review, blockers, strategic updates |
| `retrospective` | Went well, didn't go well, improvements |
| `planning` | Goals, tasks, estimates, risks |
| `brainstorm` | Ideas generated, themes, top ideas |
| `decision_meeting` | Options, pros/cons, decision, rationale, reversibility |

### 1:1 (perspective-aware)
| Type | Template Focus |
|------|---------------|
| `one_on_one_direct_report` | Manager→report: mood, wins, objectives, blockers, feedback, coaching, engagement signals |
| `one_on_one_leader` | User→boss/skip-level: direction received, leader commitments, political/strategic context |
| `one_on_one_peer` | Peer 1:1: alignment, disagreements, cross-team dependencies, commitments both ways |
| `one_on_one` | Generic 1:1 fallback when the perspective isn't identifiable |

### Exec & cross-functional
| Type | Template Focus |
|------|---------------|
| `leadership_meeting` | Peer-exec staff meeting: cross-functional decisions, priority trade-offs, resource allocation |
| `board_meeting` | Board/investor update: resolutions, management update, financial update, asks of the board |
| `architecture_review` | ADR-style: problem, options matrix, decision, reversibility, migration plan |
| `incident_review` | Blameless post-mortem: timeline, contributing factors, prevent/detect/mitigate actions |

### External
| Type | Template Focus |
|------|---------------|
| `customer_meeting` | Client requests, service feedback, blockers, commitments both ways, next steps |
| `vendor_meeting` | QBR / procurement: vendor commitments, roadmap, SLA performance, pricing, our asks |
| `interview_debrief` | Hire/no-hire recommendation with per-competency evidence and level fit |

### Fallback
| Type | Template Focus |
|------|---------------|
| `general` | Used when classification confidence is low; TL;DR + decisions + actions + open questions |

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
- [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole) (free) **or** [Rogue Amoeba Loopback](https://rogueamoeba.com/loopback/) ($99) for virtual meeting capture

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

Pick one backend (both are auto-detected — see [User Guide §3](docs/USER_GUIDE.md#3-audio-setup-macos) for full instructions):

- **BlackHole (free, default).** Set your system output to **Meeting Output** (Multi-Output Device sending audio to speakers + BlackHole). Select **Meeting Capture** (Aggregate Device combining mic + BlackHole) as input, or use auto-detect.
- **Rogue Amoeba Loopback ($99, optional).** One virtual device combining mic + system audio. No Multi-Output or Aggregate Device needed. Name the device "Meeting Capture" and auto-detect handles the rest. See [User Guide §3.9](docs/USER_GUIDE.md#39-option-b--rogue-amoeba-loopback-alternative-to-blackhole).

```yaml
# config/config.yaml
recording:
  audio_device: "Meeting Capture"   # name works for either backend; or "auto"
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
mm actions                                        # Confirmed open action items (the tracker)
mm actions --proposed                             # Proposals awaiting your review
mm actions --all-states                           # Confirmed + proposed + rejected
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
| `mm actions` | List **confirmed** open action items (supports `--owner`, `--overdue`, `--status`, `--proposed`, `--all-states`) |
| `mm actions complete <id>` | Mark action item as done |
| `mm generate <id>` | Generate minutes from transcript |
| `mm reprocess <id>` | Re-run generation + ingestion (skips transcription/diarization) |
| `mm rediarize <id>` | Re-run speaker diarization on existing audio (skips transcription) |
| `mm status <id>` | Show per-stage pipeline state for a meeting (capture → transcribe → diarize → generate → ingest → embed → export) |
| `mm resume <id>` | Resume pipeline from the first non-succeeded stage (supports `--from-stage`, `--all`) |
| `mm doctor` | Run eleven first-run diagnostic checks (Python, ffmpeg, audio device, tokens, LLM reachability, DB, disk, GPU, models, sqlite-vec, WeasyPrint natives). Supports `--json`. Exits non-zero on any failure. |
| `mm repair` | Run startup health checks and optionally rebuild derived indexes (FTS, embedding vectors, voice samples). Supports `--dry-run`, `--check=<name>`, `--yes`. |
| `mm embed` | Generate semantic search embeddings for all meetings (run once to backfill) |
| `mm embed <id>` | Embed a single meeting |
| `mm series detect` | Detect recurring-meeting series (REC-1) and upsert them |
| `mm series list` | List all detected series with cadence + member count |
| `mm series show <id>` | Show detail for a single series |
| `mm stats rebuild` | Rebuild the topic-clusters cache used by the Stats → Topics panel (ANA-1) |
| `mm export <id>` | Export a meeting to PDF / DOCX / Markdown (supports `--format`, `--with-transcript`, `--out`) |
| `mm export --series=<id>` | Bulk-export every meeting in a series as a ZIP bundle |
| `mm delete <id>` | Delete meeting and all associated data |
| `mm cleanup` | Run retention policy cleanup (delete expired data) |
| `mm generate-key` | Generate a new encryption key for at-rest encryption |
| `mm serve` | Start the web UI + API server (supports `--host`, `--port`; refuses to drift if the port is busy) |
| `mm upgrade` | Pull latest code from main and rebuild (supports `--branch` override) |
| `mm service install` | Install macOS Launch Agent for auto-start on login |
| `mm service uninstall` | Remove the macOS Launch Agent |
| `mm service start` | Start the service now |
| `mm service stop` | Stop the service |
| `mm service status` | Show service status and API health |
| `mm service logs` | Show server logs (supports `--follow`, `--lines`) |

## Pipeline state machine

Every meeting flows through a seven-stage pipeline: `capture` → `transcribe` → `diarize` → `generate` → `ingest` → `embed` → `export`. Each stage's status (`pending` / `running` / `succeeded` / `failed` / `skipped`), attempt count, timestamps, and last error are persisted in the `pipeline_stages` table, so a crash or failure at any stage is visible and recoverable.

- Run `mm status <meeting_id>` to see the current stage table.
- Run `mm resume <meeting_id>` to re-run from the first non-succeeded stage; pass `--from-stage=<stage>` to override the starting point or `--all` to resume every meeting with a failed stage.
- **On server restart** (`mm serve` lifespan): any stage still marked `running` beyond the interruption threshold (30 min) is flipped to `failed` with `last_error='interrupted'`. The server does **not** auto-resume — the user decides when to re-run.
- Retention cleanup preserves audio for meetings whose pipeline has not reached a terminal state, so `mm resume` remains possible until the meeting's final stages succeed or are skipped.

REST equivalents: `GET /api/meetings/:id/pipeline`, `POST /api/meetings/:id/resume`, `GET /api/pipeline/interrupted`.

## First-run diagnostics (`/onboarding`)

On first launch with an empty database, `mm serve` routes the browser to `/onboarding`, which calls `GET /api/doctor` and renders each of the eleven checks as a green/yellow/red card with a copy-paste fix command. Every failing check has its own **Retry** button so you can fix the underlying issue and re-check without leaving the page. From the command line, `mm doctor` prints the same table; add `--json` for programmatic consumption.

## Disk-space preflight (DSK-1)

Before starting a recording the web UI calls `GET /api/recording/preflight?planned_minutes=<n>` and compares the estimated FLAC size against the free space on the `data_dir` partition. The tier drives a warning modal:

- **green** — silent start
- **yellow** / **orange** — warning dialog with the twenty oldest audio files eligible for safe deletion (only meetings whose pipeline has reached a terminal state appear), and a "Start anyway" button
- **red** — same cleanup UI but requires an explicit "Yes, I understand" double-confirm

A watchdog thread polls free disk space every 30 seconds while recording and triggers a graceful stop if free space drops below half of the remaining estimate — this guarantees a valid (possibly truncated) FLAC rather than a corrupted tail. `mm record start` in non-interactive mode (launchd) refuses red-tier preflights; interactive shells always let you override with `--force`.

## Passive speaker identity (SPK-1)

There is no separate "enroll your voice" step. Speaker centroids are learned from real meeting audio: each time you name a speaker (e.g. `SPEAKER_00 → Jon`) in the Transcript tab, the mean embedding for that cluster is saved as a voice sample for Jon. On subsequent meetings, every new cluster is scored against every known person's centroid via cosine similarity:

- **≥ 0.85** — the Transcript tab pre-fills the name with a green **suggested** badge
- **0.70 – 0.85** — pre-fill with a yellow **? Name** badge; one click to accept, or type a different name to override
- **below 0.70** — field is blank; if the cluster has more than 30 seconds of speech, an inline "Create new person" row appears (name + optional email, `POST /api/persons`) so you can name them without leaving the page

Corrections are handled explicitly: relabeling a cluster from Jon to Sarah flips Jon's prior sample to `confirmed=false` so it stops contributing to his centroid. Clusters with less than 5 seconds of speech produce no sample at all (too noisy). Only the 20 most recent confirmed samples contribute to any given centroid, so representations stay current even as a voice changes over time. Samples live in `person_voice_samples` and are deleted by `ON DELETE CASCADE` when the person or meeting is removed — nothing extra to configure for retention.

## Recurring-meeting series (REC-1)

Meetings that share the exact attendee set and meeting type across 3+ instances are grouped into a **series** — think "weekly 1:1 with Jon" or "biweekly planning with the core team". Detection runs automatically after every pipeline completion (best-effort, never breaks the pipeline) and on demand via `mm series detect` or `POST /api/series/detect`. The cadence is classified from the median inter-meeting interval (weekly / biweekly / monthly / irregular).

Browse series at `/series` — click through to `/series/:id` for:
- A chronological timeline of all member meetings
- Cross-meeting **open action items** with a "first seen" meeting link
- **Recent decisions** across every instance
- **Recurring topics** (parking-lot entries + discussion points that appear in ≥ 2 members)

Every meeting detail page shows a "Part of series: {title} →" pill when the meeting belongs to a series. Storage: `meeting_series` + `meeting_series_members`.

## Cross-meeting analytics (ANA-1)

The Stats page adds four tabbed panels backed by pure SQL (no new persistent storage except the topic-cluster cache):

1. **Commitments** — per-person assigned / completed / overdue action-item counts over a rolling 90-day window, with a 12-week completion sparkline.
2. **Topics** — embedding clusters of `discussion_point` and `parking_lot` chunks that show up in ≥ 3 meetings **without** a decision whose embedding is close (cosine ≥ 0.7). Backed by a `topic_clusters_cache` table rebuilt lazily on page load if older than 24 h or manually via `mm stats rebuild`. Degrades gracefully to an empty state when `sqlite-vec` isn't loadable.
3. **Sentiment** — numeric timeseries using the mapping `positive=1.0, constructive=0.7, neutral=0.5, tense=0.3, negative=0.0`. Supports per-person or per-type filters via query params.
4. **Effectiveness** — per meeting type, % of meetings with each `meeting_effectiveness` attribute set (clear agenda, decisions made, actions assigned, unresolved items).

Every endpoint also accepts a `series=<series_id>` query parameter to scope the analytics to a single series's members.

## Pre-meeting briefings (BRF-1)

The `/brief` page pulls together everything you already know about a set of attendees before you walk in: last meeting, cadence, matching series, open commitments (with overdue flagged), unresolved topics, recent sentiment sparklines, recent decisions, and the top relevant transcript excerpts. A pinned "Start recording" footer lets you kick off a session with a pre-filled title, meeting type, attendee list, and a carry-forward note built from the overdue commitments.

Deep-links:
- `/people/:id` has a **Start a briefing →** button.
- `/series/:id` has a **Start a briefing for the next one →** button that passes all attendees + the meeting type through to `/brief`.

All six sections are pure SQL queries over existing tables — no LLM call by default. Flip `brief.summarize_with_llm: true` in `config.yaml` to attach a two-sentence synthesis built from the aggregates.

## Desktop notifications (NOT-1)

macOS Notification Center alerts fire on two pipeline events: every successful completion (`Meeting ready: <title>` with duration and action-item count) and any stage failure (`Pipeline failed: <title>` with stage + short error). Clicking the banner opens the meeting in the web UI. Ships disabled on non-macOS (via `pync`, which is macOS-only). Toggle in `config.yaml`:

```yaml
notifications:
  enabled: true             # defaults to platform detection (true on darwin)
  sound: true
  click_url_base: http://localhost:8080/meeting
```

## Exports (EXP-1)

Export meeting minutes to PDF, DOCX, or Markdown — with an optional `--with-transcript` flag to append the full diarized transcript. The meeting detail page has an **Export ▾** menu; the series page has an **Export all meetings (ZIP)** button that bundles every member meeting into one archive.

```bash
mm export <meeting_id> --format=pdf --with-transcript
mm export <meeting_id> --format=docx --out ~/Downloads/minutes.docx
mm export --series=<series_id> --format=pdf --out ~/Documents/
```

The HTTP surface mirrors the CLI: `GET /api/meetings/:id/export?format=pdf|docx|md[&with_transcript=true]` and `GET /api/series/:id/export?format=...`. A missing native dep (WeasyPrint requires libpango, python-docx is pure-Python) yields a clean `501` with an install hint instead of a crash.

On macOS the WeasyPrint native libs (`pango`, `cairo`, `gdk-pixbuf`, `libffi`) install automatically via `install.sh` and `mm upgrade`, and `DYLD_FALLBACK_LIBRARY_PATH` is set at process startup — fresh installs don't need any manual steps. `mm doctor` check #11 flags the dependency as warn (PDF export is optional) if anything is missing.

DOCX users can drop a styled template at `templates/export/docx_template.docx`; python-docx inherits its heading + paragraph styles.

## Action item review workflow (proposed → confirmed)

Action items extracted from a transcript are **proposals**, not commitments. Every newly-extracted item lands with `proposal_state = "proposed"` and stays out of:

- the global `/actions` tracker (default filter is **Confirmed**)
- the prior-action carryover injected into the next meeting's prompt
- the rendered `## Action Items` section in `data/minutes/{id}.md`
- the Obsidian export, DOCX export, embeddings used by the chat feature, and the `open_actions` count on the Stats overview

Until you review them. The meeting detail page's **Actions** tab shows a "N proposed actions to review" banner with **Accept all** / **Reject all**, plus per-row **Accept · Edit · Reject** controls. Editing lets you fix the wording, owner, or due date before accepting. Rejected items are kept as `proposal_state = "rejected"` so the same suggestion isn't re-confirmed by accident on regenerate.

On every Accept / Reject the server re-renders the on-disk minutes JSON + Markdown, refreshes the FTS index, and re-exports to Obsidian — so the curated set is what shows up in search, exports, and your vault. **Regenerate** a meeting and the items go back to `proposed` (you re-review). The `## Action Items` section in the markdown is empty until at least one item is confirmed.

The global `/actions` page has a chip filter — **Confirmed** (default) / **Proposed** / **All** — so you can sweep an entire backlog from one place. A small "Confirm all proposals from before a date…" affordance is there for the one-time clear right after upgrading: the migration that introduces this workflow flips every historical action item to `proposed`, so on first run after the upgrade you'll see them all queued for review.

REST surface:

- `GET /api/action-items?proposal_state={confirmed|proposed|rejected|all}` — defaults to `confirmed`.
- `PATCH /api/action-items/{id}` — accepts `{status, proposal_state, description, owner, due_date}` (all optional).
- `POST /api/action-items/bulk-review/{meeting_id}` — body `{confirm: [ids], reject: [ids]}`.
- `POST /api/action-items/confirm-before` — body `{before_date: "YYYY-MM-DD"}`, the post-migration backlog clear.

## Web UI

A browser-based interface built with Svelte + Tailwind CSS at `localhost:8080` with calendar view, action items, decisions, people, **series** (REC-1), stats (with four ANA-1 tabbed panels), recording controls, template manager, and settings.

```bash
# Start the server
mm serve

# Open in your browser
open http://localhost:8080
```

**Pages**: Meetings (calendar view with day list + inline detail + search with filters; meeting cards show an "N to review" badge when the meeting has proposed actions waiting), **Chat** (talk to your meetings — ask natural-language questions across all meeting history with citations), **Brief** (BRF-1 pre-meeting briefing with six data sections + inline Start Recording panel), Meeting Detail (Actions tab surfaces the Accept / Edit / Reject review banner for proposed items), Action Items (Confirmed / Proposed / All chip filter + admin "Confirm all proposals from before a date" sweep), Decisions, People, Stats (charts), Record (live waveform + concurrent pipeline status + live note-taking), Templates (view/edit/create prompt templates), Settings (LLM provider/model selection with custom model support, Performance & Hardware, Security, Retention, and CORS config).

**Features**: Dark mode, full-text search with `Cmd+K`, in-calendar search with type filter chips, keyboard navigation, responsive layout, meeting type color coding, WebSocket-based real-time updates, concurrent pipeline processing (record a new meeting while the previous one processes in background), auto-detect capture device, auto-save recovery every 5 minutes during recording, live note-taking during recording (title, speaker names, notes, custom LLM instructions) with markdown support, a single PREVIEW toggle that pairs the editor with a live rendered preview pane, a 2D-resizable notes textarea, tab-switch persistence (form fields survive navigation away from /record until Stop is pressed), and a confirm-gated Cancel button that discards the in-flight audio without producing a meeting record, inline-editable meeting title on the detail page (rewrites the embedded title in the minutes JSON/MD, refreshes the FTS index, and renames the Obsidian export), structured card-based minutes view with collapsible discussion topics, color-coded transcript per-speaker with inline "Name speakers" editor, **post-hoc external-notes tab** (paste notes from Teams/Zoom/Meet/Otter to auto-rename speakers + regenerate the summary), people management (edit / delete / merge duplicate entities with automatic historical attribution updates), Performance & Hardware settings (Apple Silicon MPS toggle), encryption at rest, retention policies with automatic cleanup.

### Meeting titles

Pick a title up-front during recording, or leave it blank and the LLM will generate one from the transcript. Either way, the title is editable post-hoc on the meeting detail page (click the **Edit** button next to the heading). A rename:

- updates the title in the database and the full-text-search index,
- rewrites the `# Title` heading inside `data/minutes/{id}.md` and the `metadata.title` field inside `data/minutes/{id}.json`,
- mirrors the new title to the notes sidecar so a later regeneration won't overwrite it,
- and renames the Obsidian export from `{date} {old_safe_title}.md` to `{date} {new_safe_title}.md`.

Internal data files under `recordings/`, `transcripts/`, `minutes/`, and `notes/` are keyed by `meeting_id` (UUID) and are not renamed — only their contents change where the title is embedded.

### External notes (post-hoc)

Each meeting detail page has an **External notes** tab between **Minutes** and **Transcript**. Paste the notes exported from a meeting app (Teams, Zoom, Google Meet/Gemini, Otter, etc.) and hit *Save & update minutes*. The server:

1. Archives the verbatim paste to `data/external_notes/{meeting_id}.md` (one file per meeting, overwritten on resubmit).
2. Kicks off a background job that (a) asks the LLM to map `SPEAKER_xx` labels to real names using the external notes as ground truth, and (b) re-runs the full minutes-generation pipeline with the notes injected as additional context.
3. Appends a verbatim `## External notes` section to the meeting's markdown (and Obsidian file) so your raw paste is preserved through any future regeneration.

The tab shows a status pill (`processing` / `ready` / `error`) and polls every few seconds — no manual refresh needed. Submission is async: the endpoint returns 202 immediately; the background reprocess typically takes 15–60 s depending on the model and transcript length.

### Attachments — files, links, and pasted images

Each meeting has an **Attachments** tab on the detail page; the same panel also appears on the Record page once a recording starts so you can attach context mid-meeting (drop in a slide deck, paste a chart screenshot, drop a link to the spec being discussed).

Supported inputs:

- **Files:** PDF, DOCX, PPTX, PNG, JPG, HEIC. 50 MB cap.
- **Links:** any HTTP(S) URL. Fetched server-side; readable text extracted via `trafilatura`.
- **Pasted images:** clipboard image paste captured globally on the page.

Per attachment, the worker runs in the background:

1. Extracts text — text-layer for PDF, OCR fallback for scanned PDFs (`tesseract` + `poppler`), full-document order for DOCX, slide titles + body + speaker notes for PPTX, OCR for images, readable-text extraction for links.
2. Stores the extracted content as a sidecar markdown at `data/attachments/{meeting_id}/{attachment_id}.md`.
3. Calls the LLM with a separate prompt (tiered length: short for screenshots, comprehensive for long docs) to produce a summary that grounds the extracted text. The system prompt forbids paraphrasing numbers, dates, and proper nouns — verbatim quotes only.
4. Marks the attachment `ready`.

When minutes are generated (or regenerated) for the meeting, the pipeline:

- Briefly waits for any in-flight summaries.
- Splices a `## ATTACHED MATERIAL` block into the LLM prompt — the model treats it as ground truth.
- Post-appends a verbatim `## Attachments` section to the rendered minutes (and the JSON's embedded markdown) listing each attachment with its summary and a link to the raw source, so a reader can crosscheck the minutes against the original material.

System dependencies (auto-installed by `install.sh` on macOS): `tesseract` (image and scanned-PDF OCR), `poppler` (rendering scanned PDFs to images for OCR). `mm doctor` reports both as warnings (not failures) — image and scanned-PDF attachments need them, but other kinds work without.

Attachments also feed into:

- **Search.** Extracted attachment text is folded into the FTS5 minutes index, so keyword search hits the body of attached PDFs / DOCX / OCR'd images.
- **Chat.** Each ready summary contributes one `attachment_summary` chunk to the embedding store, so "what did the Q3 deck say about EMEA?" surfaces the right material with a clear citation.
- **Speaker rename.** During minutes generation, the LLM that maps `SPEAKER_xx` labels to human names also sees attachment summaries — title-slide presenter names and explicit attendee lists are useful tie-breakers (weighted lower than meeting-app notes, since attachment authors aren't always participants).
- **Long documents.** Inputs over ~100k characters get a two-phase **map-reduce** summarization (chunk-by-chunk → synthesis) so book-length PDFs are summarized end-to-end rather than truncated.

API:

- `POST /api/meetings/{id}/attachments` — multipart upload (file).
- `POST /api/meetings/{id}/attachments/link` — JSON `{ url, title?, caption? }`.
- `GET /api/meetings/{id}/attachments` — list with status.
- `GET /api/attachments/{id}` — detail (parsed sidecar, summary, extracted text).
- `GET /api/attachments/{id}/raw` — original bytes (or 410 if pruned by retention).
- `DELETE /api/attachments/{id}`.

**Development** (with hot reload):
```bash
mm serve                          # API on :8080
cd web && npm install && npm run dev   # Svelte on :3000, proxies /api → :8080
```

## REST API

The backend is a FastAPI application serving at `:8080` covering meetings, search, action items, decisions, people (including `POST /api/persons` for inline creation), stats, recording, speaker suggestions (`GET /api/meetings/:id/speaker-suggestions`), series (`GET /api/series`, `GET /api/series/:id`, `POST /api/series/detect`, `GET /api/meetings/:id/series`), analytics panels (`GET /api/stats/commitments`, `/api/stats/sentiment`, `/api/stats/effectiveness`, `/api/stats/unresolved-topics`), and configuration. Auto-generated interactive API documentation is available at [http://localhost:8080/docs](http://localhost:8080/docs).

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
  engine: pyannote                  # pyannote (local) | pyannote-ai (cloud) | pyannote-mlx (Apple Silicon)
  model: pyannote/speaker-diarization-community-1
  # Cloud backend (engine: pyannote-ai) — paid, hosted by the pyannote authors.
  # Install: pip install -e '.[diarize-cloud]'  + set $PYANNOTEAI_API_KEY
  pyannote_ai:
    tier: community-1               # community-1 (€0.04/hr) | precision-2 (€0.11/hr, best DER)
  # MLX backend (engine: pyannote-mlx) — experimental Apple Silicon path.
  # Install: pip install -e '.[diarize-mlx]'

generation:
  llm:
    primary_provider: anthropic       # anthropic | openai | openrouter | ollama
    model: claude-sonnet-4-6          # Model ID (provider-specific)
    temperature: 0.2
    ollama:
      base_url: http://localhost:11434  # Ollama server URL
      timeout_seconds: 300              # Local models can be slower
  vendors: [AWS, NetApp]              # Per-vendor feedback sub-sections in templates
  length_mode: concise                # concise | standard | verbose — detailed-notes length
  generate_email_draft: true          # Emit a follow-up email draft artifact
  confidentiality_default: auto       # auto | public | internal | confidential | restricted
  close_acknowledged_actions: true    # Auto-close prior open actions acknowledged in a meeting
  prior_actions_lookback_meetings: 5  # How many prior meetings to pull open actions from

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
│   │   ├── diarize.py         #   DiarizationEngine — façade over pluggable backends
│   │   ├── diarization_backends/   # pyannote-local | pyannote-ai | pyannote-mlx
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

Copyright © 2026 Jurgen Hofkens. All rights reserved.

This project is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0) — see [LICENSE](LICENSE) for the full text.

In short:
- **Personal, research, and non-commercial use** — free, under AGPL-3.0.
- **Modifications, forks, and network-delivered services** built on this code must also be released under AGPL-3.0, with full source available to users.
- **Commercial use that cannot comply with AGPL-3.0** (e.g. you want to incorporate this into a closed-source product or service) requires a separate commercial license — see [COMMERCIAL.md](COMMERCIAL.md).

Contributions are welcome under the [Contributor License Agreement](CLA.md) — see [CONTRIBUTING.md](CONTRIBUTING.md) for how to submit a pull request.
