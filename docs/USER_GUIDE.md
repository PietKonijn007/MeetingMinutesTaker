# Meeting Minutes Taker — User Guide

This guide walks you through installing the system, setting up audio capture, configuring the pipeline, and using both the CLI and web UI to record, transcribe, and search your meetings.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Audio Setup (macOS)](#3-audio-setup-macos)
4. [Configuration](#4-configuration)
5. [API Keys](#5-api-keys)
6. [Local AI Setup (Ollama)](#6-local-ai-setup-ollama)
7. [Using the CLI](#7-using-the-cli)
8. [Using the Web UI](#8-using-the-web-ui)
9. [Pipeline Modes](#9-pipeline-modes)
10. [Customizing Templates](#10-customizing-templates)
11. [Template Manager (Web UI)](#11-template-manager-web-ui)
12. [Managing Your Data](#12-managing-your-data)
13. [Encryption at Rest](#13-encryption-at-rest)
14. [Retention Policies](#14-retention-policies)
15. [Troubleshooting](#15-troubleshooting)

### Batch 3 features (shipped in Phases 0–4)

16. [First-Run Onboarding (`mm doctor` / `/onboarding`)](#16-first-run-onboarding)
17. [Pipeline Resume (`mm status` / `mm resume`)](#17-pipeline-resume)
18. [Health Checks and Repair (`mm repair`)](#18-health-checks-and-repair)
19. [Disk-Space Preflight and Advisory Cleanup](#19-disk-space-preflight)
20. [Speaker Identity Learning (SPK-1)](#20-speaker-identity-learning)
21. [Recurring Meetings and Series](#21-recurring-meetings-and-series)
22. [Cross-Meeting Analytics](#22-cross-meeting-analytics)
23. [Pre-Meeting Briefing (`/brief`)](#23-pre-meeting-briefing)
24. [Desktop Notifications](#24-desktop-notifications)
25. [Export to PDF and DOCX (`mm export`)](#25-export-to-pdf-and-docx)

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

To capture both sides of a virtual meeting (your voice **and** the remote participants), you need a virtual audio loopback. Two routes are supported:

- **Option A — BlackHole** (free, open-source, recommended default). Requires creating a Multi-Output Device and an Aggregate Device in Audio MIDI Setup. Sections 3.1 – 3.8 below.
- **Option B — Rogue Amoeba Loopback** ($99, commercial). Single-app setup: one virtual device combines mic + system audio in one place, no Audio MIDI Setup steps needed. Section 3.9 below.

Both paths produce a device the app will auto-detect. Pick one — they're interchangeable from the app's perspective.

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

### 3.9 Option B — Rogue Amoeba Loopback (alternative to BlackHole)

[Loopback](https://rogueamoeba.com/loopback/) is a paid commercial app ($99) that replaces the BlackHole + Multi-Output + Aggregate Device setup with a single pre-mixed virtual device. If you already own it, or prefer a GUI over Audio MIDI Setup, use this instead of §3.1 – 3.7. The rest of the app (auto-detect, transcription, pipeline) is identical — the app detects both backends the same way.

**What Loopback gives you that BlackHole doesn't:**
- Single virtual device combining mic + system audio, no Audio MIDI Setup needed
- Per-application source routing (capture Zoom only — not Slack pings, not Spotify)
- Drag-and-drop UI, survives OS updates more reliably, full vendor support

**Trade-off:** $99 one-time. BlackHole is $0.

#### 3.9.1 Install Loopback

Download from [rogueamoeba.com/loopback](https://rogueamoeba.com/loopback/) and install. Launch the app once and grant it the permissions it requests (microphone + audio routing).

#### 3.9.2 Create a virtual device

The typical workflow is: **AirPods (or any headset) for both your voice and the meeting audio you hear, laptop untouched.** This works in Loopback, but there's one critical setting without which it will feed back catastrophically. Follow the steps *in order*.

1. In the Loopback app, click **New Virtual Device** (bottom-left).
2. Name it **Meeting Capture** — this is what lets the Meeting Minutes app auto-detect it.
3. In the **Sources** column, click **+** and add exactly two sources:
   - **Jurgen's AirPods** (or your wired/Bluetooth headset mic)
   - **Pass-Thru** — the system-audio side that would otherwise need BlackHole
4. In the **Output Channels** section, leave the default 2-channel mapping.
5. In the **Monitors** column (right side), click **+** and add **Jurgen's AirPods** (the same AirPods you added as a Source). This is what plays the meeting audio into your ears.
6. **🚨 Critical anti-feedback step — do not skip this.** In the **Sources** column, expand **Options** on the **Jurgen's AirPods** source box and toggle **"Mute in Monitors"** on.
   - Alternative: in the routing grid, click the dot on the line running from the AirPods *Source* row to the AirPods *Monitor* row to sever it.
   - Verify: the teal line from the AirPods Source should go **left to Output Channels** (recorded) but should **not** reach the AirPods Monitor box on the right.

**Why step 6 matters.** Without it, AirPods speaker plays the meeting → AirPods mic picks up its own speaker output → Loopback re-sends that back to the AirPods speaker → runaway feedback loop. Muting the mic source in the monitor breaks the loop while still letting the mic reach the recording.

##### Two honest tradeoffs of this setup

- **Small amount of meeting audio bleeds into your voice track.** Your AirPods mic physically picks up a little of what plays in the AirPods speaker (acoustic leakage through your ear canal). Loopback has no acoustic echo cancellation. The transcript handles this fine; it's just audible on raw playback.
- **Bluetooth drops to the low-quality handset codec.** The moment macOS activates the AirPods mic, Bluetooth switches from A2DP (44 kHz stereo, hi-fi) to HFP (8 kHz mono, phone-call quality). This affects both what you hear *and* what gets recorded, and it's a hard Bluetooth-protocol limit — not a Loopback issue. Wired earbuds or headsets have no codec degradation.

If audio quality matters more than using AirPods end-to-end, see the **alternative setup** below.

##### Global rules that always apply

> 🚨 Three rules that prevent audio feedback — follow all three no matter which setup you pick:
>
> 1. **If the same device is both a Source and a Monitor, you must enable "Mute in Monitors" on its Source.** AirPods / wired headsets / any combo mic+speaker device. This is the #1 feedback cause.
> 2. **Never add built-in Speakers as a Monitor while any mic is a Source.** The built-in mic will acoustically hear the speakers across the desk → feedback. Headphones / AirPods are sealed; speakers are not.
> 3. **Remove any "⚠ Device Missing" entries from Sources and Monitors.** Disconnected Bluetooth devices stay listed, trigger "Missing Monitor Device" warnings, and can shadow real routing.

##### Alternative: built-in mic + AirPods monitor (best audio quality)

If you prefer maximum audio fidelity — broadcast-grade voice, hi-fi stereo in your ears — swap the Source:

- **Sources:** MacBook Air Microphone + Pass-Thru
- **Monitors:** Jurgen's AirPods (no "Mute in Monitors" needed, different device)

Because the AirPods mic is never activated, macOS keeps them in A2DP (hi-fi stereo) mode. The MacBook Air 3-mic array is genuinely broadcast-quality for a seated laptop. Downside: if you walk around, you walk away from your mic.

For either setup you still want **no Multi-Output Device and no Aggregate Device** — that's the whole point of using Loopback.

#### 3.9.3 Tell the app to use it

If you named the device **Meeting Capture**, auto-detect picks it up — no config change needed. Otherwise, set the exact Loopback device name in `config/config.yaml`:

```yaml
recording:
  audio_device: "Meeting Capture"   # or "Loopback Audio" (default Loopback name)
```

You can also leave it as `"auto"` — the app's auto-detect prefers devices named "Meeting Capture" or containing "Loopback" in their name.

#### 3.9.4 Audio signal flow with Loopback (AirPods both ends)

```
You speak
    │
    ▼
  🎧 AirPods Mic ────────────────┐
                                  │ (Mute in Monitors: ON — no loopback)
  Remote participants             │
        │ (Zoom/Teams)            │
        ▼                         ▼
  macOS System Output ──► Pass-Thru ──► Output Channels
  (set to Meeting Capture)                     │
                                               ├──► Meeting Minutes Taker (recording)
                                               │
                                               └──► Monitor ──► 🎧 AirPods Speaker
                                                                 (you hear the meeting)
```

Key points:
- Your voice enters via the AirPods mic Source → Output Channels → recording. It does **not** loop back to the AirPods speaker (that's what "Mute in Monitors" prevents).
- Meeting audio enters via Pass-Thru (because macOS system output is set to Meeting Capture) → Output Channels → both the recording and the AirPods speaker.
- Compare to §3.6: no Multi-Output Device in the chain, and system-audio capture is per-app rather than global.

#### 3.9.5 Verifying detection

After configuring, run:

```bash
mm doctor
```

Check #3 (**meeting_capture_device**) should pass and report "Meeting Capture / BlackHole / Loopback device available." If it fails with Loopback installed, confirm the Loopback app is running (it must be open for the virtual device to appear in Core Audio) and that at least one source is mapped.

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
  primary_engine: whisper                 # whisper (faster-whisper, default) | whisper-cpp (GGML)
  whisper_model: medium                   # tiny | base | small | medium | large-v3
                                          #   tiny:  ~1 GB RAM, fast, lower accuracy
                                          #   base:  ~1 GB RAM, good for short meetings
                                          #   small: ~2 GB RAM, good balance
                                          #   medium: ~5 GB RAM, recommended default
                                          #   large-v3: ~10 GB RAM, best accuracy
                                          # Distil models also available:
                                          #   distil-medium.en, distil-large-v3 (5-6x faster)
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
  # Vendors rendered as per-vendor service-feedback sub-sections inside templates
  # that include a vendor-feedback block. Empty list = single generic block.
  vendors: [AWS, NetApp]
  # Length of the `detailed_notes` narrative. `concise` targets ~150-400 words
  # (recommended for executive readers), `standard` ~400-900, `verbose` ~900-1500.
  length_mode: concise                    # concise | standard | verbose
  # Emit a ready-to-send follow-up email draft as a structured field + markdown
  # section. Set false to suppress.
  generate_email_draft: true
  # Confidentiality handling. `auto` asks the LLM to classify
  # (public | internal | confidential | restricted); set to a fixed value to
  # force a floor.
  confidentiality_default: auto
  # Prior-action carryover: pull still-open action items from recent meetings
  # that share attendees and inject them into the prompt. The LLM can mark
  # any that were acknowledged-closed in this meeting; matching DB rows are
  # then set to `done` / `in_progress` / `cancelled` during ingestion.
  close_acknowledged_actions: true
  prior_actions_lookback_meetings: 5      # How many prior meetings to scan
  llm:
    primary_provider: anthropic           # anthropic | openai | openrouter | ollama
    model: claude-sonnet-4-6              # Model for minutes generation
    fallback_provider: null                # Fallback provider (null = disabled, or "openai")
    fallback_model: gpt-4o                # Fallback model (used when fallback_provider is set)
    # OpenRouter models use provider-prefixed IDs, e.g.:
    #   anthropic/claude-sonnet-4, google/gemini-2.5-pro-preview,
    #   openai/gpt-4o, meta-llama/llama-4-maverick, deepseek/deepseek-r1
    # Ollama models: use the model name as shown by `ollama list`, e.g.:
    #   qwen2.5:14b, llama3.1:8b, phi4:14b, mistral-small:24b
    temperature: 0.2                      # Low = more factual, less creative
    max_output_tokens: 4096               # Max length of generated minutes
    retry_attempts: 3                     # Retries on API failure
    timeout_seconds: 120                  # API call timeout
    ollama:                               # Ollama-specific settings
      base_url: "http://localhost:11434"  # Override with OLLAMA_BASE_URL env var
      timeout_seconds: 300                # Local models can be slower

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
    model: claude-sonnet-4-6
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

### 5.1 Anthropic (default provider for minutes generation)

Get your API key from [console.anthropic.com](https://console.anthropic.com/).

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Add this to your shell profile (`~/.zshrc` or `~/.bashrc`) to persist it.

### 5.2 OpenRouter (optional — access 200+ models)

OpenRouter provides a unified API to access models from Anthropic, Google, OpenAI, Meta, DeepSeek, Mistral, and many more. Get your API key from [openrouter.ai/keys](https://openrouter.ai/keys).

```bash
export OPENROUTER_API_KEY="sk-or-..."
```

To use OpenRouter as your primary provider, set it in the config:

```yaml
generation:
  llm:
    primary_provider: openrouter
    model: anthropic/claude-sonnet-4       # or google/gemini-2.5-pro-preview, etc.
```

Popular OpenRouter models:

| Model ID | Provider | Notes |
|----------|----------|-------|
| `anthropic/claude-sonnet-4` | Anthropic | Strong general-purpose |
| `anthropic/claude-haiku-4` | Anthropic | Fast and cheap |
| `google/gemini-2.5-pro-preview` | Google | Large context window |
| `google/gemini-2.5-flash-preview` | Google | Fast and cheap |
| `openai/gpt-4o` | OpenAI | Strong general-purpose |
| `openai/gpt-4o-mini` | OpenAI | Fast and cheap |
| `meta-llama/llama-4-maverick` | Meta | Open-source |
| `deepseek/deepseek-r1` | DeepSeek | Reasoning-focused |
| `mistralai/mistral-medium-3` | Mistral | European alternative |

See [openrouter.ai/models](https://openrouter.ai/models) for all available models. You can also enter any model ID directly in the Settings UI — custom models are saved to the dropdown after successful use.

### 5.3 OpenAI (optional fallback)

If you configure an OpenAI fallback:

```bash
export OPENAI_API_KEY="sk-..."
```

### 5.4 HuggingFace (required for speaker diarization)

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

**Additional requirements for pyannote.audio 3.3+:**
- `ffmpeg` — `brew install ffmpeg` (the installer does this automatically in step 2.5/10)
- `torchcodec` — installed via `pip install torchcodec` (included in `pyproject.toml` dependencies)

If you don't want diarization, set `diarization.enabled: false` in the config and skip this step.

### 5.4.1 Speaker name mapping

When you enter speaker names in the Record page's live note-taking field (e.g., `Tom, Mary`), the names are assigned to diarization labels **in order of first-speaking**:

- Whoever talks first → `Tom`
- Whoever talks second → `Mary`

These names replace `SPEAKER_00`, `SPEAKER_01` everywhere — transcript segments, LLM prompt, generated minutes, and the Transcript tab display.

**If you forgot to enter names or got the order wrong**, open the meeting in the web UI → Transcript tab → click **"✎ Name speakers"**. A small inline editor lets you assign names to each `SPEAKER_XX` label. Clicking **"Save & regenerate minutes"** rewrites the transcript JSON and regenerates the minutes with correct names (takes ~30s).

### 5.4.2 Re-running diarization on existing meetings

If a meeting was processed before you accepted the pyannote license or installed ffmpeg, diarization will have returned zero segments — no speaker labels anywhere. Instead of re-recording:

```bash
mm rediarize <meeting_id>
```

This re-runs only diarization on the existing audio file (~30s to 3min depending on length and hardware), merges speaker labels into the existing transcript, and regenerates minutes.

### 5.5 Using a `.env` file

Instead of setting environment variables in your shell, you can create a `.env` file at the project root. Values in `.env` take priority over variables set in the shell environment.

```
# .env (at the root of MeetingMinutesTaker/)
ANTHROPIC_API_KEY=sk-ant-...
HF_TOKEN=hf_...
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
```

The file is loaded automatically at startup via `env.py`. You do not need to source or export it.

---

## 6. Local AI Setup (Ollama)

Ollama lets you run LLMs locally for **free, private, offline** meeting summarization. No API keys or cloud services needed.

### 6.1 Install Ollama

**macOS:**
```bash
brew install ollama
```

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:** Download from [ollama.com/download](https://ollama.com/download).

### 6.2 Start Ollama and pull a model

```bash
ollama serve                    # Start the Ollama server (runs on port 11434)
ollama pull qwen2.5:14b         # Pull a recommended model (~10GB)
```

**Recommended models by hardware:**

| Your Hardware | Recommended Model | Pull Command |
|---------------|-------------------|--------------|
| 8GB RAM, no GPU | `qwen2.5:7b` | `ollama pull qwen2.5:7b` |
| 16GB RAM or 8GB GPU | `qwen2.5:14b` | `ollama pull qwen2.5:14b` |
| 32GB+ RAM or Apple Silicon | `qwen2.5:32b` | `ollama pull qwen2.5:32b` |
| 48GB+ GPU/RAM | `qwen2.5:72b` | `ollama pull qwen2.5:72b` |

The Settings page shows hardware-detected recommendations — visit `http://localhost:8080/settings` after starting the server.

### 6.3 Configure the app to use Ollama

In the web UI Settings page, select **Ollama (local)** as the LLM provider and choose your model from the dropdown (auto-populated from your local Ollama instance).

Or edit `config/config.yaml`:

```yaml
generation:
  llm:
    primary_provider: ollama
    model: qwen2.5:14b
```

### 6.4 Whisper.cpp transcription engine

Whisper.cpp is installed automatically by `install.sh` and `mm upgrade`, compiled with hardware-specific acceleration:

| Platform | Acceleration |
|----------|--------------|
| Apple Silicon (M1-M4) | Metal + Accelerate framework |
| Intel Mac | Accelerate framework |
| Linux + NVIDIA GPU | CUDA (via `WHISPER_CUDA=1`) |
| Linux + AMD GPU | ROCm/HIPBLAS |
| Linux CPU | OpenBLAS (if available) |

The installer detects your hardware and forces a source build with the correct flags so the engine can use your GPU. Verify the install with:

```bash
python -c "import pywhispercpp; print(pywhispercpp.__version__)"
```

To use Whisper.cpp instead of the default Faster Whisper, set the engine in config (or pick it in the Settings page):

```yaml
transcription:
  primary_engine: whisper-cpp    # Uses GGML quantized models
  whisper_model: medium
```

If the build fails (missing cmake, no compiler, etc.), Faster Whisper continues to work as the default — only the alternative engine is unavailable.

### 6.5 Hardware detection

The app auto-detects your GPU and RAM to recommend models. Check your hardware profile:

```bash
curl http://localhost:8080/api/config/hardware | python -m json.tool
```

This returns your GPU type, VRAM, RAM, and recommended Whisper + Ollama models.

### 6.6 Running fully offline

With Ollama for summarization and local Whisper for transcription, the entire pipeline runs offline. The only external dependency is pyannote.audio for speaker diarization (which requires a one-time model download). Set `diarization.enabled: false` to skip this if needed.

### 6.7 Performance & Hardware settings

The Settings page has a **Performance & Hardware** section that controls hardware acceleration flags applied at service startup. Currently exposes:

**MPS CPU fallback (Apple Silicon)** — Toggle (default: on). Sets the `PYTORCH_ENABLE_MPS_FALLBACK=1` environment variable. When pyannote's Metal GPU backend hits an op that isn't supported on MPS, it silently falls back to CPU for that op instead of crashing. Without this, pyannote diarization raises `NotImplementedError` on Apple Silicon for certain models.

**Impact on diarization speed:**

| Setup | Approx. time for 13-min audio |
|-------|-------------------------------|
| Apple Silicon + MPS (default) | 1-3 min |
| Apple Silicon + MPS with CPU fallback (recommended) | 2-5 min |
| Apple Silicon CPU only | 12-20 min |
| NVIDIA CUDA | 30-90 sec |

The toggle equivalent in YAML:

```yaml
performance:
  pytorch_mps_fallback: true    # default
```

**Important:** changes require a service restart (`mm service stop && mm service start`) to take effect — the env var is read once at process startup.

**Verifying Metal is in use:**

```bash
# Look for this line in logs after starting a diarization:
grep -i "diarization:" ~/MeetingMinutesTaker/logs/server.log | tail -3
# Expected: "Diarization: using Apple Silicon GPU (MPS)"

# Or use asitop for a live GPU dashboard:
.venv/bin/pip install asitop
sudo asitop
# GPU should jump to 50-95% during pyannote inference
```

---

## 6.8 Semantic search & chat ("Talk to your meetings")

The system can embed all your meetings into a vector database for semantic search and AI-powered Q&A.

### First-time setup: backfill embeddings

After upgrading, run this once to embed all existing meetings:

```bash
mm embed                    # Embed all meetings (~2s each)
mm embed --force            # Re-embed everything (if you want a fresh index)
mm embed <meeting_id>       # Embed a single meeting
```

New meetings are automatically embedded during the pipeline (after ingestion).

### Using the Chat page

Open the **Chat** page from the sidebar. Ask natural-language questions like:

- _"Summarize all meetings with Jon Porter since April"_
- _"What decisions were made about the product roadmap?"_
- _"Show me open action items for the marketing team"_
- _"What risks were raised in our last planning session?"_

The system:
1. Parses your query to extract filters (person, date range, topic focus)
2. Searches across all embedded meeting chunks (transcripts, summaries, action items, decisions, discussions)
3. Combines keyword search (FTS5) + semantic search (vector similarity)
4. Sends the most relevant chunks to your configured LLM
5. Returns a synthesized answer with clickable meeting citations

**Chat history** is saved in the sidebar. Click any previous conversation to continue it.

### How embeddings work

Each meeting is chunked into embeddable pieces:
- **Summary** (1 chunk)
- **Discussion points** (1 chunk each)
- **Action items** (1 chunk each, tagged with owner)
- **Decisions** (1 chunk each, tagged with decision-maker)
- **Risks, follow-ups** (1 chunk each)
- **Transcript** (sliding window, ~400 tokens per chunk with overlap)

Each chunk is embedded with `BAAI/bge-small-en-v1.5` (384-dim, ~130MB model, runs locally on CPU or MPS). Vectors are stored in `sqlite-vec` inside the same `meetings.db` file — so **backups automatically include embeddings**.

---

## 7. Using the CLI

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

# Re-run generation + ingestion (skips transcription/diarization).
# Use when you changed prompt templates or speaker names and want
# to regenerate minutes without paying the transcription cost.
mm reprocess <meeting_id>

# Re-run ONLY speaker diarization on existing audio (skips transcription).
# Use when diarization was broken at the time of original recording
# (missing HF_TOKEN / ffmpeg / torchcodec) and you want to add speaker
# labels without re-transcribing. Also re-runs minutes generation by default.
mm rediarize <meeting_id>

# Just update the transcript's speakers without regenerating minutes
mm rediarize <meeting_id> --skip-regenerate

# Delete a meeting and all its data
mm delete <meeting_id>

# Delete without confirmation prompt
mm delete <meeting_id> --yes

# Pull latest code from main and rebuild everything
# (switches branch if needed, reinstalls deps, rebuilds frontend,
#  restarts service). Use --branch to pull from a specific branch.
mm upgrade
mm upgrade --branch main           # explicit
mm upgrade --no-restart            # update code but don't restart service
```

### 6.8 Batch 3 commands (Phases 0–4)

These commands shipped with the Batch 3 feature set. See sections 16–25 at the end of this guide for workflow context.

```bash
# ── First-run diagnostics (ONB-1) ─────────────────────────────
mm doctor                          # 11 health checks with pass/warn/fail status
mm doctor --json                   # machine-readable for scripting

# ── Health & repair (HLT-1) ───────────────────────────────────
mm repair --dry-run                # see what would be repaired without writing
mm repair                          # repair all failing checks (prompts before each)
mm repair --check=<name>           # repair only one check (e.g. fts, embeddings)
mm repair --yes                    # skip the per-check confirmation prompt

# ── Pipeline state machine (PIP-1) ────────────────────────────
mm status <meeting_id>             # per-stage pipeline state (colored)
mm resume <meeting_id>             # resume from the first non-succeeded stage
mm resume <meeting_id> --from-stage=generate   # force-start from a specific stage
mm resume --all                    # resume every meeting with a failed stage

# ── Recording with disk preflight (DSK-1) ─────────────────────
mm record start --planned-minutes=90    # size preflight against 90-min recording
mm record start --force                  # skip the preflight warning entirely

# ── Recurring-meeting series (REC-1) ──────────────────────────
mm series detect                   # one-shot detection across your archive
mm series list                     # all series with attendee names + cadence
mm series show <series_id>         # full series detail

# ── Cross-meeting analytics (ANA-1) ───────────────────────────
mm stats rebuild                   # rebuild the topic-clusters cache (Panel 2)

# ── Export to PDF / DOCX / Markdown (EXP-1) ───────────────────
mm export <meeting_id> --format=pdf
mm export <meeting_id> --format=docx --with-transcript
mm export <meeting_id> --format=md --out=/path/to/out.md
mm export --series=<series_id> --format=pdf   # bulk → ZIP
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
| **Onboarding** | `/onboarding` | 11 diagnostic checks with per-check retry and copy-paste fix hints. Auto-opens on first visit when the `meetings` table is empty. |
| **Meetings** | `/` | Calendar view with day list and inline meeting detail. Filter by type, search. |
| **Meeting Detail** | `/meeting/:id` | Full minutes, transcript with audio player, action items, decisions, tags. **Export dropdown** (Markdown / PDF / DOCX / Obsidian) next to the Regenerate button. Shows "Part of series: ... →" when the meeting belongs to a recurring series. |
| **Action Items** | `/actions` | All action items across meetings. Filter by owner, status, overdue. Check items off. |
| **Decisions** | `/decisions` | Chronological log of all decisions, grouped by date. |
| **Series** | `/series` | All detected recurring meeting series. Shows title, cadence (weekly/biweekly/monthly), member count, and last meeting date. |
| **Series Detail** | `/series/:id` | Timeline of all member meetings, cross-meeting action items, decisions, recurring topics, plus a "Export all meetings (ZIP)" bulk action. |
| **Brief** | `/brief` | Pre-meeting briefing with six pure-query sections (who/when last, open commitments, unresolved topics, sentiment sparklines, recent decisions, context excerpts) plus a pinned Start Recording panel pre-filled with speakers (via SPK-1) and carry-over notes. |
| **Chat** | `/chat` | Talk to your meetings — ask natural-language questions across all meeting history with citations. |
| **People** | `/people` | Directory of everyone who has appeared in meetings, with meeting counts and action items. Click a person to edit, delete, or merge. Each person page has a "Start a briefing →" link. |
| **Stats** | `/stats` | Tabbed dashboard: Meetings (existing charts), Commitments (per-person action completion), Topics (recurring unresolved topics), Sentiment (per-person trends), Effectiveness (per-meeting-type). |
| **Record** | `/record` | Start/stop recording with live timer, audio levels, auto-detected device. Pre-flight disk check warns if free space is tight and offers inline cleanup of the oldest audio files. Shows concurrent pipeline job status. |
| **Templates** | `/templates` | View, edit, and create meeting prompt templates. Built-in templates are protected from deletion. |
| **Settings** | `/settings` | Visual configuration editor for all settings (audio device, Whisper model, LLM, pipeline mode, notifications, brief summarization). |

### 7.3 Navigation

- **Sidebar**: Always visible on desktop (collapses on mobile). Shows all pages (including Templates) plus a recording status indicator.
- **Search**: Global search bar in the top bar. Press `Cmd+K` (or `Ctrl+K`) from anywhere to focus it.
- **Dark mode**: Toggle via the sun/moon icon in the top-right corner. Follows your system preference on first visit.

### 7.4 Meetings page (Calendar View)

The default landing page is a calendar-first view with two panels:

- **Left panel**: A search bar at the top for full-text search across all meetings, followed by a month calendar with colored dots on days that have meetings. Below the calendar, either search results or a list of meetings for the selected day. Click any day to see its meetings.
- **Right panel**: Meeting detail rendered inline — clicking a meeting in the day list (or a search result) shows its full minutes, transcript, and actions without navigating away from the page.

Search features in the calendar view:
- **Search bar**: Type a query above the calendar to search across all meetings. Results replace the day list and show title, date, type badge, and snippet.
- **Debounced search**: Results update automatically after 300ms of no typing.
- **Click to view**: Click any search result to load the meeting detail in the right panel.
- **Clear search**: Click the X button or the "Clear" link to return to the normal calendar view.

### 7.5 Meeting detail page

The richest page. Shows everything about one meeting.

**Header**: Title, metadata pills (type, duration, date, attendee count), attendee list, tags. Hover over the title and an **Edit** button appears next to it — click to rename the meeting inline. `Enter` saves, `Esc` cancels. Renaming rewrites the embedded `# Title` heading in `data/minutes/{id}.md` and the `metadata.title` field in `data/minutes/{id}.json`, refreshes the full-text-search index, mirrors the new title to `data/notes/{id}.json` (so a later regeneration won't overwrite it), and renames the Obsidian export file. The internal data files in `recordings/`, `transcripts/`, `minutes/`, and `notes/` are keyed by `meeting_id` (UUID) and stay put — only their contents change where the title is embedded.

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
- **Live note-taking**: While recording, you can enter a meeting title, speaker names (comma-separated), free-form notes, and custom instructions for the LLM. The title is optional — set one yourself for a stable, predictable name, or leave it blank and the LLM generates one from the transcript. A user-set title is honored verbatim and the LLM title-generation step is skipped. Speaker names help the diarization engine map speakers. Notes are included as context in the minutes generation prompt. Custom instructions let you tell the LLM to focus on specific topics or use a particular format. Notes are saved to `data/notes/{meeting_id}.json` and automatically loaded during pipeline processing. The title is also editable post-hoc on the meeting detail page (see §7.5).
- **Processing state**: Below the recording controls, a "Processing" section shows per-meeting pipeline status. Each job displays steps — Transcribe, Generate, Index — with checkmarks as they complete. Pipeline jobs run sequentially (queued) to avoid memory thrashing with Whisper.

You can record a new meeting immediately after stopping the previous one — the previous meeting's pipeline continues processing in the background.

Recording and pipeline status updates are delivered in real time via WebSocket push (no polling). Each pipeline job is tracked with step/progress/error and auto-cleans up after 60 seconds.

### 7.9 Settings page

A visual editor for `config/config.yaml`. Organized into sections:

| Section | Settings |
|---------|----------|
| **Recording** | Audio device (dropdown of detected devices), sample rate, auto-stop silence threshold |
| **Transcription** | Whisper model (with size/accuracy descriptions, including Distil-Whisper), language |
| **Speaker ID** | Enable/disable diarization |
| **Minutes Generation** | LLM provider (Anthropic, OpenAI, OpenRouter, Ollama), model (dropdown with built-in + previously used custom models, text input for custom model IDs), temperature, max tokens |
| **Pipeline** | Mode (automatic / semi-automatic / manual) |
| **Storage** | Database path, data directory |
| **Security** | Encryption at rest toggle, encryption key path, generate key button |
| **Retention** | Enable/disable retention policies, audio/transcript/minutes retention days |
| **API** | CORS origins, host, port |
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

### 7.5 Managing People

The People page (`/people`) lists every attendee across all your meetings, automatically deduplicated by name and email. Because diarization produces labels and different meetings sometimes use different spellings of the same name (e.g. "Tom" and "Tom Hankins"), the system often creates duplicate person entities you'll want to clean up.

Click any person to open their detail page, which shows their meeting history, action items, and decisions. Three actions are available:

**✎ Edit** — change name and/or email. Renaming automatically updates all references to the old name in action_items (owner) and decisions (made_by), so historical attributions stay consistent. You'll see a 409 error if another person already uses the email you're trying to set — use Merge instead.

**Merge…** — combine duplicate entities. Opens a modal with a dropdown of all other people (sorted alphabetically, each showing their meeting count for easy identification). Pick the target (the entity to keep), optionally tick "Rename owner/maker in action items and decisions" (default: on), and click Merge. What happens:
- Every meeting the source attended gets reassigned to the target (deduplicated if the target was already on the same meeting)
- Source email is carried over to the target if the target had no email
- Historical `owner` and `made_by` strings are rewritten (if the checkbox was ticked)
- The source person is deleted, and you land on the target's page

**Delete** — remove the person entirely from your directory and from all meeting attendee lists. Historical `owner`/`made_by` strings in action items and decisions are preserved (they're stored as strings, not FKs), so your meeting history stays readable. Confirmation modal prevents accidental clicks.

**Typical cleanup workflow**: open each person flagged as a dup, click Merge, pick the canonical entity, confirm. Takes ~5 seconds per merge.

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

Meeting minutes are generated using Jinja2 templates in the `templates/` directory. Each meeting type has its own template. A shared `_shared.md.j2` macro file provides cross-cutting blocks (TL;DR, omit-empty rule, length guidance, vendor feedback injection, risks, open questions, prior-action carryover, email draft, confidentiality) that every template imports.

### 9.1 Built-in templates

**Team & cadence**

| File | Meeting Type | When Used |
|------|-------------|-----------|
| `standup.md.j2` | Daily standup | Per-person Done/Today/Blockers |
| `team_meeting.md.j2` | Team meeting | Decisions, financial review, blockers, strategic updates, action items |
| `retrospective.md.j2` | Retrospective | Went well, didn't go well, stop/start/continue, improvements |
| `planning.md.j2` | Sprint/project planning | Scope, priorities, assignments, timeline, risks |
| `brainstorm.md.j2` | Brainstorming session | Ideas generated, themes, top ideas |
| `decision_meeting.md.j2` | Decision meeting | Options, pros/cons, decision, rationale, reversibility |

**1:1 (perspective-aware)**

| File | Meeting Type | When Used |
|------|-------------|-----------|
| `one_on_one_direct_report.md.j2` | 1:1 with a direct report | Manager perspective: mood, wins, objectives, blockers, feedback, coaching, engagement |
| `one_on_one_leader.md.j2` | 1:1 with own manager / skip-level | User is the report: direction received, leader commitments, political/strategic context |
| `one_on_one_peer.md.j2` | Peer 1:1 (no reporting line) | Alignment, disagreements, cross-team dependencies, commitments both ways |
| `one_on_one.md.j2` | Generic 1:1 fallback | When the perspective isn't identifiable |

**Exec & cross-functional**

| File | Meeting Type | When Used |
|------|-------------|-----------|
| `leadership_meeting.md.j2` | Peer-exec staff meeting | Cross-functional decisions, priority trade-offs, resource allocation |
| `board_meeting.md.j2` | Board / investor update | Formal resolutions, management update, financials, asks of the board |
| `architecture_review.md.j2` | Architecture / design review | ADR-style: problem, options matrix, decision, reversibility, migration plan |
| `incident_review.md.j2` | Incident / post-mortem | Blameless timeline, contributing factors, prevent/detect/mitigate actions |

**External**

| File | Meeting Type | When Used |
|------|-------------|-----------|
| `customer_meeting.md.j2` | Client / external call | Requirements, service feedback, blockers, commitments both ways, next steps |
| `vendor_meeting.md.j2` | Vendor / partner / procurement | Vendor commitments, roadmap, SLA, pricing, our asks and escalations |
| `interview_debrief.md.j2` | Candidate interview debrief | Hire/no-hire with per-competency evidence and level fit |

**Fallback**

| File | Meeting Type | When Used |
|------|-------------|-----------|
| `general.md.j2` | Other / fallback | TL;DR + decisions + actions + open questions; used when classification confidence is low |

Every template emits the shared baseline (TL;DR, risks, open questions, action items, decisions, email draft, confidentiality) plus its type-specific sections. **Empty sections are omitted entirely** — no "Not discussed" placeholders.

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
| `vendors` | `list[str]` | From `generation.vendors` — renders per-vendor feedback sub-sections |
| `length_mode` | `str` | From `generation.length_mode` — `concise` / `standard` / `verbose` |
| `prior_actions` | `list[dict]` | Open action items from recent meetings sharing attendees (for carryover); each has `id`, `description`, `owner`, `due_date`, `meeting_title` |

### 9.3 Creating a custom template

Built-in templates already cover board meetings, leadership syncs, incident reviews, vendor QBRs, architecture reviews, interview debriefs, and three 1:1 variants. You only need a new template when you have a distinct meeting shape the built-ins don't fit (e.g. a specific internal ritual).

1. Create a new `.md.j2` file in `templates/` and import the shared macros — this gives you TL;DR, omit-empty, risks, open questions, email draft, confidentiality, and vendor injection for free:

```jinja
{# templates/strategy_offsite.md.j2 #}
Summarize a multi-day strategy offsite. Focus on cross-functional alignment,
long-range bets, and the commitments each leader made.

---
{% import '_shared.md.j2' as m %}
{{ m.meeting_header('Strategy Offsite', title, date, duration, attendees, organizer) }}

---

{{ m.omission_rule() }}

{{ m.length_guidance(length_mode) }}

{{ m.tldr_block() }}

## Title
[Specific — name the strategic theme.]

## Summary
3-5 sentences: key strategic decisions, top bets committed to, biggest unresolved tension.

## Detailed Notes
Narrative walkthrough of each session.

## Strategic Bets
For each: bet / timeframe / owner / success criteria / investment.

## Cross-Functional Commitments
Populate `action_items` — one owner per commitment.

{{ m.vendor_feedback_block(vendors) }}

{{ m.risks_block() }}

{{ m.open_questions_block() }}

{{ m.prior_actions_block(prior_actions) }}

## Key Topics
5-10 short labels.

{{ m.email_draft_block() }}

{{ m.confidentiality_block() }}
```

2. The file stem becomes the meeting type. Override it when generating:

```bash
mm generate <meeting_id> --type strategy_offsite
```

Shared macro files are conventionally named with a leading underscore (`_shared.md.j2`, `_your_macros.md.j2`) — the router excludes underscore-prefixed files from the type list automatically.

---

## 10. Template Manager (Web UI)

The Template Manager lets you view, edit, and create meeting prompt templates directly from the browser at `/templates`.

### 10.1 Viewing and editing templates

All `.md.j2` template files from the `templates/` directory appear in the Template Manager. Click any template to view its contents in an editor. Built-in templates (standup, the three `one_on_one_*` variants, team/leadership/board, decision/architecture/incident reviews, customer/vendor, brainstorm, retrospective, planning, interview_debrief, and the `general` fallback) are protected from deletion but can be edited. The `_shared.md.j2` macro include is also editable — changes propagate to every template that imports it.

### 10.2 Creating custom meeting types

To create a new meeting type:

1. Go to the Templates page and click **New Template**.
2. Enter a name (e.g., `board_meeting`). This creates `templates/board_meeting.md.j2`.
3. Write your prompt template using Jinja2 syntax (see section 9 for template variables).
4. Save. The new type is immediately available as a valid meeting type throughout the system.

Any `.md.j2` file you add to the `templates/` directory — whether via the Template Manager or by creating the file directly — becomes a valid meeting type.

### 10.3 LLM-Based Meeting Type Classification

The system uses an LLM classifier (Claude Haiku) to automatically detect meeting types. When the initial signals-based classifier produces a confidence score below 0.7, the LLM classifier is invoked automatically. It sends the first 4000 characters of the transcript along with metadata (speaker count, calendar title, attendee count) to the LLM, which returns the meeting type, a confidence score, and reasoning.

The classifier reads actual template descriptions (system prompt and section headings) from your templates directory to make its decision, so custom template types are auto-discovered. The cost is approximately $0.001 per classification. If the Anthropic API is unavailable, the system falls back to a heuristic classifier that uses the calendar title first (e.g. "QBR" → `vendor_meeting`, "post-mortem" → `incident_review`, "board meeting" → `board_meeting`), then content keywords, then attendee count (two attendees → `one_on_one`). The fallback returns low confidence so the LLM path is always preferred when reachable.

You can always override the detected type manually:

```bash
mm generate <meeting_id> --type leadership_meeting
```

### 10.4 TL;DR, confidentiality, and prior-action carryover

Every generated minutes document now opens with a **TL;DR** (~100-word executive digest covering the biggest decision, biggest risk, most urgent action, and the single takeaway) and carries a **confidentiality** classification in the header (`public` / `internal` / `confidential` / `restricted`). Every template also emits an **Open Questions** section for items raised but not resolved, and a **Follow-up Email Draft** (subject + to/cc + body) when the meeting produced commitments worth sending around.

**Prior-action carryover** (`generation.close_acknowledged_actions`): before generation, the pipeline pulls still-open action items from recent meetings that share attendees (up to `generation.prior_actions_lookback_meetings`, default 5) and injects them into the prompt. The LLM can mark any that were acknowledged as done / in progress / cancelled in the current meeting. During ingestion those DB rows are updated automatically — no manual checkbox click needed.

### 10.5 Vendor feedback injection

Templates that include a vendor-feedback block render one sub-heading per vendor listed in `generation.vendors` (default `[AWS, NetApp]`). Change the list to match your stack — e.g. `[GCP, Snowflake, Databricks]` — and every type-specific template reflects it without edits. The block is still omitted entirely when no vendor feedback was raised during the meeting.

### 10.6 Template highlights

- **`one_on_one_direct_report`** — manager perspective: Mood/Energy, Accomplishments & Wins (dated), Progress Against Objectives, Blockers (4 categories with handling + support needed), Feedback Given (SBI format), Feedback Received (upward), Vendor/Tooling Feedback, Career Development, Coaching Notes, Engagement Signals, split Action Items (employee vs manager commitments).
- **`one_on_one_leader`** — user-is-the-report perspective: Direction Received, Feedback Received, Feedback Given (upward), Leader Commitments (captured verbatim as action items with the leader as owner), Strategic & Political Context.
- **`one_on_one_peer`** — peer 1:1: Alignment Reached, Disagreements / Open Tensions, Cross-Team Dependencies, Commitments (both directions).
- **`team_meeting`** — Decisions with rationale, Financial Review (cloud spend, optimization, P&L), Blockers (4 categories + Cross-Team Dependencies), Strategic Updates, Technology Decisions, Vendor Feedback, Customer Impact, Resource & Capacity, Team Health, Announcements, Parking Lot, Action Items (by urgency).
- **`leadership_meeting`** — Cross-Functional Decisions, Priority & Resource Trade-offs, Cross-Team Commitments, Strategic Alignment, Organizational Signals, Financial & Commercial Signals.
- **`board_meeting`** — Resolutions Passed (exact wording + vote count), Management Update, Financial Update, Strategic Items, Risks & Mitigations, Asks of the Board, Board Feedback & Direction, Executive Session (presence only — never fabricated content).
- **`architecture_review`** — Problem Statement, Requirements & Constraints, Options Considered (pros/cons/cost/risks), Evaluation Matrix, Decision (with reversibility), Migration / Rollout Plan.
- **`incident_review`** — Incident Summary (severity, impact, timestamps), Timeline, Impact, Contributing Factors, What Went Well / Poorly, Corrective Actions (tagged **[Prevent] / [Detect] / [Mitigate]**), Customer Communication Follow-ups. Blameless framing.
- **`vendor_meeting`** — Vendor Commitments (the headline output — vendor as action item owner), Roadmap & Feature Updates, SLA Performance, Commercial / Pricing, Our Asks / Escalations, Competitive Context, Our Action Items.
- **`customer_meeting`** — Customer Requirements, Vendor Feedback (per configured vendor), Customer Blockers (4 categories), Demo Notes, split Commitments (ours vs customer's), Next Steps, Competitive Intelligence.
- **`interview_debrief`** — Panel Recommendation + Level Consistency, Per-Interviewer Signal, Per-Competency Assessment, Strengths, Concerns / Gaps, Missing Data, Compensation / Level Fit.

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

## 12. Encryption at Rest

Meeting Minutes Taker supports optional encryption at rest for audio files, transcripts, and minutes using Fernet symmetric encryption.

### 12.1 Generating an encryption key

Generate a new encryption key via the CLI or web UI:

```bash
mm generate-key
```

Or use the "Generate Key" button in the Settings > Security section of the web UI. The key is saved to the path specified in `security.encryption_key_path` in your config.

### 12.2 Enabling encryption

```yaml
# config/config.yaml
security:
  encryption_enabled: true
  encryption_key_path: config/encryption.key
```

Once enabled, new files written by the pipeline (audio, transcripts, minutes) are encrypted. Existing files are not retroactively encrypted.

### 12.3 Key management

**Warning**: If you lose your encryption key, encrypted data cannot be recovered. Back up the key file separately from the data it protects.

---

## 13. Retention Policies

Configure automatic cleanup of old data to manage disk space.

### 13.1 Configuration

```yaml
# config/config.yaml
retention:
  enabled: true
  audio_retention_days: 90        # Delete audio files older than 90 days
  transcript_retention_days: null  # null = keep forever
  minutes_retention_days: null     # null = keep forever
```

### 13.2 Running cleanup

Cleanup runs via the CLI:

```bash
mm cleanup
```

Or from the web UI Settings > Retention section using the "Run Cleanup" button. The cleanup process checks file ages against the configured retention periods and deletes expired files.

### 13.3 Retention status

Check what would be cleaned up before running:

```bash
# Via the API
curl http://localhost:8080/api/retention/status
```

Or view the status in the web UI Settings > Retention section.

---

## 16. First-Run Onboarding

When you start `mm serve` for the first time on a fresh install, the web UI auto-redirects to `/onboarding` — a single page showing eleven diagnostic checks with color-coded status and copy-paste fix hints. It covers:

1. Python version (needs 3.11+)
2. `ffmpeg` on PATH
3. BlackHole aggregate device present (macOS)
4. HuggingFace token + pyannote model license accepted
5. LLM provider reachable (dry-run completion)
6. Database integrity (`PRAGMA integrity_check`)
7. Free disk space vs. retention settings
8. GPU detection (MPS / CUDA / CPU fallback)
9. Whisper model files present
10. `sqlite-vec` extension loadable
11. **WeasyPrint native libs** loadable (for PDF export; warn-level because optional)

Each failing check shows a hint — for example, missing `ffmpeg` gets `Run: brew install ffmpeg` with a clipboard-copy icon. A per-check "Retry" button re-runs just that check without reloading the page.

### From the CLI

```bash
mm doctor           # prints the full table; exit 0 if no failures
mm doctor --json    # machine-readable (used internally by /onboarding)
```

### When to revisit

- After upgrading to a new major version (dependencies or models may have changed).
- When something stops working — `mm doctor` is usually faster than reading logs.
- Before re-enabling a disabled feature (e.g. flipping diarization on).

The sidebar does not link to `/onboarding` by design; navigate to it directly when you need it.

---

## 17. Pipeline Resume

The pipeline is split into seven stages: `capture → transcribe → diarize → generate → ingest → embed → export`. Each stage's state is persisted to a `pipeline_stages` table, so a crash, a killed process, or an LLM failure mid-run no longer wastes prior work.

### Seeing per-meeting state

```bash
mm status <meeting_id>
```

Prints a table with stage, status (`pending | running | succeeded | failed | skipped`), attempt count, last error, and timestamps.

### Resuming

```bash
mm resume <meeting_id>                         # from first non-succeeded stage
mm resume <meeting_id> --from-stage=generate   # force from a specific stage
mm resume --all                                # every meeting with a failed stage
```

`capture` never re-runs (audio can't be programmatically re-recorded); `transcribe` + `diarize` always re-run together if either needs it.

### Automatic supervisor

On server startup, any stage still marked `running` older than 30 minutes is flipped to `failed` with `last_error='interrupted'`. This happens automatically — the server logs `"Reset N interrupted pipeline stages"`. You then decide whether to resume.

### API surface

- `GET /api/meetings/:id/pipeline` — list per-stage state
- `POST /api/meetings/:id/resume` — kicks off resume in the background (202 + `job_ref`)
- `GET /api/pipeline/interrupted` — all meetings with any failed or pending stage

### Interaction with retention

Audio files for meetings whose pipeline hasn't reached a terminal state are **preserved** by retention cleanup, even if they'd otherwise be age-eligible for deletion. Once `ingest` (or a later stage) succeeds, normal retention resumes.

---

## 18. Health Checks and Repair

`mm repair` runs the same six integrity checks that happen at server startup, and offers to fix the repairable ones.

### The six checks

1. `PRAGMA integrity_check` — full SQLite integrity. Repair is unsafe here; escalates to a warning.
2. `meetings_fts` row count matches `meetings` — rebuilds the FTS index on repair.
3. Every `embedding_chunks.chunk_id` has a matching `embedding_vectors` row — re-embeds orphans.
4. Every final meeting has a minutes row — warn-only (LLM work required to fix).
5. Every `audio_file_path` either exists on disk or retention deleted it — warn-only.
6. Every `person_voice_samples.meeting_id` still exists — orphan cleanup.

### Running it

```bash
mm repair --dry-run           # show the plan; no writes
mm repair                     # prompt before each repair
mm repair --yes               # repair all without prompting
mm repair --check=fts         # repair a single named check
```

### API

`GET /api/health/full` returns the full report as JSON. The startup supervisor logs the same summary; failed checks do **not** auto-repair — you decide via the CLI.

### When to run it

- After an unclean shutdown.
- If search or chat starts returning stale results (FTS index drift).
- After restoring from a backup.
- Before filing a bug — a clean `mm repair --dry-run` output is often informative in itself.

---

## 19. Disk-Space Preflight

Before recording starts (via the web UI or `mm record start`), the app checks free disk space against an estimate of how much a planned-length FLAC will consume. The default planned length is 60 minutes; override with `--planned-minutes`.

### Tiers

| Tier | Free vs. estimated | Behavior |
|---|---|---|
| green  | ≥ 2× | Silent start. |
| yellow | 1.2–2× | Warning modal with top-20-oldest-audio cleanup table; user can "Start anyway". |
| orange | 1–1.2× | Stronger warning with same cleanup UI; user can still start anyway. |
| red    | < 1× | Double-confirm warning ("Yes, I understand"); still allows start. |

The CLI adds one extra guardrail: **non-interactive** mode (launchd / systemd) refuses red-tier starts. Use `--force` to override.

### Mid-recording watchdog

While recording, a daemon thread polls free-disk-space every 30 seconds. If free space drops below `0.5 × remaining_estimated_size`, the recording triggers a **graceful stop** — flushes the audio buffer, closes the FLAC file cleanly, and marks the meeting with an early-stop reason.

### Cleanup helper

The web modal lists the 20 oldest audio files **from meetings whose pipeline has reached a terminal state** (safe to delete). Tick the checkboxes, click "Delete selected" — these call `DELETE /api/retention/audio` with the meeting-id list. Non-terminal meetings' audio is never offered for deletion.

---

## 20. Speaker Identity Learning

The app learns each person's voice from the meetings you actually record. There is **no 30-second enrollment step**.

### How it works

1. **First meeting for a person.** You map `SPEAKER_XX → Jon` in the usual Transcript-tab speaker rename UI. The pyannote embedding for that cluster is persisted to `person_voice_samples` as Jon's first confirmed sample.
2. **Subsequent meetings.** Each cluster's embedding is cosine-matched against every person's centroid (mean of their last 20 confirmed samples):
   - **≥ 0.85** — name is **pre-filled** with a green "suggested" badge.
   - **0.70 – 0.85** — name is pre-filled with a yellow "?" badge. Confirm with Save; correct by picking a different person.
   - **< 0.70** — blank field, manual selection. For clusters with **> 30 seconds of speech** and no match at all, an inline "Create new person" form offers to add them on the spot.
3. **You hit Save.** The suggestions you accept become confirmed samples; the centroid recomputes automatically on the next match.

### Corrections are handled correctly

If you relabel a cluster (say you accepted "Jon" but it was actually "Sarah"), the system flips the sample previously added under Jon to `confirmed=false` and writes a new confirmed sample under Sarah. Drift across meetings is bounded: only the 20 most-recent confirmed samples per person feed the centroid.

### Cold start

The first **~3 meetings** for a given person still need manual naming — you can't match against a centroid that doesn't exist yet.

### Clusters the system ignores

- **< 5 seconds of speech** — too noisy to produce a reliable embedding. No sample row is written, no suggestion is offered.

### Backfilling existing meetings

There is **no one-shot backfill command** yet. To seed centroids from meetings you recorded before SPK-1 shipped:

1. `mm rediarize <meeting_id>` — re-runs pyannote on existing audio; SPK-1 now writes unconfirmed sample rows.
2. Open the meeting in the web UI → Transcript tab → hit **Save** on the speaker names (they're pre-filled from the prior mapping). Save calls `PATCH /api/meetings/:id/transcript/speakers`, which confirms the samples.

A dedicated `mm spk1 backfill` command is documented as a follow-up in `specs/07-implementation-plan-batch3.md`.

---

## 21. Recurring Meetings and Series

The app automatically detects recurring meetings — same attendee set, same meeting type, same cadence — and groups them into a **series**. Your weekly 1:1 with Jon becomes one series containing 20 meetings rather than 20 independent rows.

### When detection runs

- **Automatically**, best-effort, after each pipeline completes (runs inside a try/except — failures never block the pipeline).
- **On demand** via `mm series detect` or `POST /api/series/detect` — useful after a bulk import or when you want to re-detect immediately.

### Detection heuristic

- Group meetings by `(meeting_type, attendee_hash)` where `attendee_hash` is a stable hash of sorted attendee IDs.
- Require **≥ 3 meetings** per group.
- Require **exact attendee-set match**. (80% overlap matching is a documented follow-up.)
- Cadence classified from the median inter-meeting interval: 5–10 days → weekly; 11–18 → biweekly; 24–35 → monthly; else → irregular.

### CLI

```bash
mm series detect              # run detection; prints diff of what changed
mm series list                # all series, sorted by last meeting
mm series show <series_id>    # full detail
```

### Web UI

- **Sidebar → Series** — lists every series with title, cadence, member count, last meeting date.
- **`/series/:id`** — timeline of member meetings, cross-meeting action items, decisions, recurring topics, plus a bulk "Export all meetings (ZIP)" button.
- **Meeting detail pages** — show "Part of series: ... →" when the meeting belongs to one.
- **Series detail also links to `/brief`** — "Start a briefing for the next one →" deep-links with the series's attendees pre-filled.

### API surface

- `GET /api/series` — list all series
- `GET /api/series/:id` — detail with aggregates
- `POST /api/series/detect` — run detection now
- `GET /api/meetings/:id/series` — the series this meeting belongs to, if any
- `GET /api/series/:id/export?format=pdf` — bulk series export (ZIP)

---

## 22. Cross-Meeting Analytics

The `/stats` page has a tab bar. The original "Meetings" tab (meetings-over-time, by-type donut, action velocity, top attendees) is unchanged. Four new tabs turn the archive from episodic into longitudinal:

### Commitments

Per-person action-item completion rate over a rolling window. For each person: assigned count, completed count, overdue count, completion rate, and a 12-week sparkline of completed-per-week. API: `GET /api/stats/commitments?days=90&meeting_type=one_on_one`.

### Topics

Recurring topics that have come up in **3 or more meetings** but have **no corresponding decision** recorded. Powered by sqlite-vec approximate nearest-neighbour clustering (cosine ≥ 0.8 between topic chunks; resolved-filter removes clusters where a decision within cosine ≥ 0.7 exists).

Clusters are cached in `topic_clusters_cache` and rebuilt on page load when the cache is older than 24 h, or manually:

```bash
mm stats rebuild
```

If `sqlite-vec` isn't loadable on your install, this panel returns an empty list with a `disabled_reason` — everything else in `/stats` still works.

### Sentiment

Line chart of sentiment over time, one line per person or per meeting type. Sentiment strings from `StructuredMinutesResponse` are mapped to numeric scores: `positive=1.0, constructive=0.7, neutral=0.5, tense=0.3, negative=0.0`.

### Effectiveness

Bar chart per meeting type: % of meetings with each of the four boolean effectiveness flags (had clear agenda, decisions made, action items assigned, unresolved items).

### Series-scoped analytics

All four endpoints accept `?series=<series_id>` to restrict aggregation to the members of one series. The series detail page (`/series/:id`) can embed these panels scoped to the series.

---

## 23. Pre-Meeting Briefing

The `/brief` page consolidates the archive around a specific upcoming meeting. Pass `?person=<id>` (repeatable) and optionally `&type=<meeting_type>`.

### Six sections rendered (all pure queries — no LLM call by default)

1. **Who & when last** — attendee cards, last meeting date, cadence; surfaces the `series_id` if the attendees match one.
2. **Open commitments** — action items assigned to the attendees, overdue flagged.
3. **Unresolved topics** — recent `parking_lot` entries involving the attendees, newest first.
4. **Recent sentiment** — micro-trend sparklines over the last 5 meetings, per person.
5. **Recent decisions** — decisions involving these attendees, with one-sentence rationale.
6. **Context excerpts** — top-3 retrieved transcript chunks via the existing embedding engine (falls back to newest summary/discussion chunks when semantic search is unavailable).

### Inline Start Recording panel (pinned footer)

- Editable title (auto-generated: "1:1 with Jon — 2026-04-20")
- Meeting-type dropdown (pre-selected from URL or inferred)
- Attendee list **pre-filled with SPK-1 centroid suggestions** so you don't have to re-type names
- Live-note textarea pre-seeded with a "Carry-forward" bullet list of open commitments
- **Start recording** button → `POST /api/recording/start` → navigate to `/record`

### Optional LLM summary

Gated by config `brief.summarize_with_llm` (default `false`). When enabled, a single 2-sentence LLM synthesis runs and its output goes into an extra `summary` field.

### Deep-links

- `/people/:id` has a "Start a briefing →" button
- `/series/:id` has a "Start a briefing for the next one →" button that passes all series attendees

---

## 24. Desktop Notifications

On macOS, the app fires a native desktop notification when a pipeline stage reaches `succeeded` for the final stage (effectively "pipeline complete") or `failed` for any stage.

### Config

```yaml
notifications:
  enabled: true              # default true on macOS, false elsewhere
  sound: true
```

### Behavior

- Notification title: "Meeting ready: {title}" or "Pipeline failed: {title}"
- Body shows duration + action-item count (complete) or stage + short error (failed)
- Click URL opens `http://localhost:8080/meeting/{meeting_id}` in your default browser. A custom `mm://` scheme is a documented follow-up.
- `pync` is lazy-imported and platform-gated — Linux/Windows no-op cleanly with an INFO log once.

Notifications never block or raise into the pipeline (wrapped in try/except at every call site).

---

## 25. Export to PDF and DOCX

Each meeting can be exported to **Markdown** (existing behavior), **PDF** (via WeasyPrint), or **DOCX** (via python-docx). A whole series can be exported to a ZIP.

### From the CLI

```bash
mm export <meeting_id> --format=pdf
mm export <meeting_id> --format=docx --with-transcript
mm export <meeting_id> --format=md --out=/path/to/out.md
mm export --series=<series_id> --format=pdf   # bulk → ZIP
```

Default output path: `data/exports/{YYYY-MM-DD}_{slug(title)}.{ext}`.

### From the web UI

- **Meeting detail page** has an **Export ▾** dropdown next to the Regenerate button with options for Markdown / PDF / DOCX / Obsidian, plus an "Include full transcript" toggle.
- **Series detail page** has an "Export all meetings (ZIP)" button.

### From the API

- `GET /api/meetings/:id/export?format=pdf|docx|md&with_transcript=false|true` — streams the file
- `GET /api/series/:id/export?format=pdf` — streams a ZIP

### PDF — WeasyPrint native deps

PDF export needs native `pango`, `cairo`, `gdk-pixbuf`, `libffi` on macOS. These are **installed automatically** by `install.sh` and `mm upgrade` via Homebrew, and `AppConfig.model_post_init()` sets `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` (or `/usr/local/lib` on Intel Macs) at runtime. You shouldn't need to do anything manually.

If the natives are somehow missing, the PDF endpoint returns a clean 501 with an install hint instead of crashing, and `mm doctor` check #11 flags it as a warning.

### DOCX template

If `templates/export/docx_template.docx` exists, python-docx inherits paragraph and heading styles from it — drop in your corporate template there. Otherwise, plain defaults are used. Action items render as a proper Word table with Description / Owner / Due / Priority / Status columns.

---

## 14. Troubleshooting

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
| **Distil-Whisper models** | Distil-Whisper models (`distil-medium.en`, `distil-large-v3`) are faster but English-only. If you need multilingual support, use the standard Whisper models. |
| **Metal acceleration not working** | On Apple Silicon Macs, Metal acceleration is used automatically by `faster-whisper`. If it falls back to CPU, check that your `ctranslate2` installation supports Metal: `pip install --upgrade ctranslate2`. |
| **NumPy compatibility error with pyannote** | If you see errors about NumPy version incompatibility, pin NumPy: `pip install "numpy<2.0"`. The pyannote.audio library may not yet support NumPy 2.x. |

### Speaker diarization issues

| Problem | Solution |
|---------|----------|
| **Diarization fails with 403 or "gated repo" error** | You must accept the license on **all three** HuggingFace model pages: `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`, and `pyannote/speaker-diarization-community-1`. Missing any one of them causes this error. |
| **Diarization fails with `use_auth_token` error** | The pyannote.audio pipeline now uses `token=` instead of `use_auth_token`. Make sure your `HF_TOKEN` environment variable is set and you are using a recent version of the app. |
| **Diarization fails with `name 'AudioDecoder' is not defined`** | pyannote.audio 3.3+ uses `torchcodec`, which requires `ffmpeg`. Run: `brew install ffmpeg && .venv/bin/pip install torchcodec`. As a fallback, you can pin pyannote: `.venv/bin/pip install 'pyannote.audio>=3.1,<3.3'`. |
| **Native sample rate mismatch** | The app automatically queries the audio device for its native sample rate and uses it for capture. If you see sample rate warnings, check that your audio device supports the detected rate. |

### Minutes generation issues

| Problem | Solution |
|---------|----------|
| **"ANTHROPIC_API_KEY not set"** | Set the env var: `export ANTHROPIC_API_KEY="sk-ant-..."` (or use OpenRouter/OpenAI as your provider instead) |
| **"OPENROUTER_API_KEY not set"** | Set the env var: `export OPENROUTER_API_KEY="sk-or-..."` (only needed if using OpenRouter as provider) |
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
| **`mm serve` fails to start** | Make sure FastAPI and uvicorn are installed: `pip install -e ".[dev]"`. Check that port 8080 isn't already in use (`mm serve` will now prompt you to kill the holding process or pick the next free port). For launchd service conflicts, run `mm service stop` before `mm serve`. |
| **`mm serve` says port in use** | `mm serve` auto-detects busy ports and offers options: `kill` (terminates the process holding it), `next` (scans ports +1 to +19 for a free one), or `abort`. In non-interactive contexts (launchd/systemd), it auto-picks the next free port. Use `--no-auto-port` to disable auto-finding. |
| **Blank page at localhost:8080** | The Svelte frontend needs to be built first: `cd web && npm install && npm run build`. In development, use `npm run dev` on port 3000 instead. |
| **API returns 404 for everything** | The database may not be initialized. Run `alembic upgrade head` to create tables. |
| **"CORS error" in browser console** | This only happens if you access the Svelte dev server directly without the proxy. Make sure `vite.config.js` proxies `/api` to `:8080`, or access the API server directly at `:8080`. |
| **Dark mode doesn't persist** | The preference is stored in `localStorage`. Clear your browser storage if it gets stuck, or toggle it again. |
| **Charts not rendering on Stats page** | Chart.js must be installed: `cd web && npm install`. If charts appear but are blank, there may be no meeting data yet — record a few meetings first. |
| **Recording page shows "disconnected"** | The WebSocket connection to the server failed. Make sure `mm serve` is running. The page will auto-reconnect when the server comes back. |
| **Settings don't save** | Check that `config/config.yaml` is writable. The API writes changes to the YAML file on disk. |

### Batch 3 features

| Problem | Solution |
|---------|----------|
| **Meeting is stuck — `mm status` shows `generate` as `running` for hours** | Server probably crashed. Restart `mm serve`; the supervisor will flip stale `running` rows (>30 min old) to `failed/interrupted`. Then: `mm resume <meeting_id>`. |
| **Search returns stale or missing results after a crash** | FTS index may have drifted. Run `mm repair --dry-run` first to see, then `mm repair` to rebuild `meetings_fts`. |
| **Chat / semantic search stops working** | Embedding vectors may have orphaned. `mm repair` — check 3 re-embeds any missing chunks. |
| **Recording won't start — red-tier disk warning** | Free disk space is below planned recording size. Either use the inline cleanup modal to delete old audio, or pass `--force` to start anyway. For launchd-managed services, add `--force` to the service config if intentional. |
| **`mm doctor` says pyannote license not accepted** | Visit the three model pages listed in section 5.4 and accept each. Re-run `mm doctor`. |
| **`mm doctor` fails check 5 (LLM provider)** | Provider is unreachable. For Anthropic/OpenAI: verify API key env var is set. For Ollama: confirm `ollama serve` is running at the configured URL. |
| **Speaker suggestions never appear** | Cold start — need ≥ 3 confirmed meetings for a person before suggestions trigger. After that, check the Transcript tab; high-confidence matches show a green "suggested" badge on the dropdown. |
| **Wrong speaker suggestion sticks across meetings** | Relabel the cluster (pick the correct person in the dropdown). This automatically invalidates the wrong sample and writes a new one under the correct person — the next meeting's suggestion will use the corrected centroid. |
| **`mm series detect` finds no series** | Need ≥ 3 meetings with **exact** same attendee set + same meeting type. Check the People page for duplicate persons (same person, two entries) and merge them first — duplicates break the attendee-set match. |
| **`/stats` Topics panel shows "sqlite-vec not available"** | The extension isn't loading. Try `pip install -e .` to rebuild. Fallback: the other three analytics tabs still work. |
| **`/stats` Topics panel is empty** | Expected if you haven't accumulated 3+ meetings discussing the same subject without a decision. Also try `mm stats rebuild` to force a cache refresh. |
| **PDF export returns 501** | WeasyPrint native libs missing. On macOS, run `mm upgrade` (which now auto-installs `pango cairo gdk-pixbuf libffi`) or manually: `brew install pango cairo gdk-pixbuf libffi`. Verify with `mm doctor` — check 11 should show green. |
| **PDF export fails silently / empty file** | Check the server logs — WeasyPrint may have crashed on specific content. Try `--with-transcript=false` to narrow it down. Markdown and DOCX exports don't have native deps and are good fallbacks. |
| **No desktop notification after pipeline completes** | On macOS only; confirm `notifications.enabled: true` in config. First-time setup may need to approve notifications in System Settings → Notifications → Terminal/Python. On non-macOS, notifications no-op (a web-UI toast mirror is a documented follow-up). |
| **Export dropdown hidden / clipped at bottom of viewport** | Fixed in a recent release — the menu now flips upward automatically when space-below is tight. If you're on an older build, run `mm upgrade`. |
