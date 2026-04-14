# System 1: Meeting Recording & Transcription

## Overview

A local-first recording and transcription system that captures audio from virtual and physical meetings by listening to system audio devices, then produces structured JSON transcripts with rich metadata.

---

## 1. Audio Capture

### 1.1 Virtual Meeting Support

| Platform | Capture Method |
|----------|---------------|
| Zoom | System audio loopback (captures both local mic + remote participants) |
| Microsoft Teams | System audio loopback |
| Slack Huddles | System audio loopback |
| Google Meet | System audio loopback (via browser tab audio) |
| WebEx | System audio loopback |
| Generic | Any application producing audio through system output devices |

### 1.2 Physical Meeting Support

- Capture from selected microphone input device (built-in mic, external mic, USB conference speaker)
- Support for multi-channel microphone arrays for improved speaker separation
- Option to select specific input device from system audio devices

### 1.3 Audio Routing

- **macOS**: Use [BlackHole](https://github.com/ExistentialAudio/BlackHole) virtual audio driver with a Multi-Output Device and Aggregate Device to capture system output + microphone input simultaneously
- **Windows**: Use WASAPI loopback capture or virtual audio cable
- **Linux**: PulseAudio/PipeWire monitor sources

#### 1.3.1 BlackHole Setup (macOS)

BlackHole is a free, open-source macOS virtual audio loopback driver that routes audio between applications with zero additional latency. We use BlackHole 2ch for this project.

##### Step 1: Install BlackHole 2ch

Option A — Homebrew:

```bash
brew install --cask blackhole-2ch
```

Option B — Download the installer from [existentialaudio.com](https://existentialaudio.com/blackhole/) or [GitHub](https://github.com/ExistentialAudio/BlackHole). Run the `.pkg` installer and restart when prompted.

After installation, verify BlackHole appears in Audio MIDI Setup (Spotlight → `Audio MIDI Setup` → check the device list for "BlackHole 2ch").

##### Step 2: Create a Multi-Output Device

A Multi-Output Device sends audio to multiple destinations at once — your speakers (so you can hear the meeting) and BlackHole (so the app can capture it).

1. Open **Audio MIDI Setup** (`⌘ + Space` → type `Audio MIDI Setup`)
2. If the Audio Devices window doesn't appear, select **Window → Audio Devices**
3. Click the **+** button in the bottom-left corner → **Create Multi-Output Device**
4. In the new device, check the boxes for:
   - **Built-in Output** (or MacBook Pro Speakers) — must be the top/first device
   - **BlackHole 2ch**
5. Enable **Drift Correction** for BlackHole 2ch (leave it off for the clock source device, which is the top device)
6. Right-click the Multi-Output Device → **Use This Device For Sound Output**

> ⚠️ The Built-in Output must be listed first (as the clock/primary device). If BlackHole appears first, uncheck and re-check the Built-in Output to reorder.

> ⚠️ macOS does not allow volume control on Multi-Output Devices. Adjust volume on individual sub-devices in Audio MIDI Setup, or control volume within the meeting application.

##### Step 3: Create an Aggregate Device

An Aggregate Device combines multiple audio devices into a single virtual device with both inputs and outputs. This lets the app capture system audio (via BlackHole) and microphone input simultaneously through one device.

1. In **Audio MIDI Setup**, click **+** → **Create Aggregate Device**
2. Check the boxes for:
   - **Built-in Microphone** (or your preferred external mic) — should be the top/first device
   - **BlackHole 2ch**
3. Enable **Drift Correction** for BlackHole 2ch
4. Rename the Aggregate Device to something descriptive, e.g. `MeetingCapture`

> ⚠️ The Built-in Microphone (or Built-in Output) must be listed as the top device in the Aggregate due to macOS requirements.

##### Step 4: Configure the Application

In the Meeting Minutes Taker configuration, set the audio device to the Aggregate Device:

```yaml
recording:
  audio_device: "MeetingCapture"   # Name of the Aggregate Device created above
```

The app will receive:
- **Channels 1–2**: Built-in Microphone input (your voice / room audio)
- **Channels 3–4**: BlackHole 2ch input (system/meeting audio from remote participants)

##### Summary of Audio Signal Flow

```
Remote participants (Zoom/Teams/etc.)
        │
        ▼
  System Audio Output
        │
        ├──► Multi-Output Device
        │         ├──► Built-in Output (speakers/headphones — you hear audio)
        │         └──► BlackHole 2ch (virtual loopback)
        │
        ▼
  BlackHole 2ch
        │
        ├──► Aggregate Device ("MeetingCapture")
        │         ├──► Built-in Microphone (your voice)
        │         └──► BlackHole 2ch (remote participants' audio)
        │
        ▼
  Meeting Minutes Taker (captures both streams)
```

##### Troubleshooting

- **No audio captured**: Verify the Multi-Output Device is set as the system sound output (right-click → "Use This Device For Sound Output")
- **No remote audio**: Ensure the meeting app is outputting to the system default (which should be the Multi-Output Device)
- **Audio glitches**: Enable Drift Correction on all non-clock devices; ensure BlackHole 2ch is not the primary/clock device
- **BlackHole not visible**: Restart CoreAudio with `sudo killall -9 coreaudiod` or reboot
- **AirPods issues**: AirPods run at a lower sample rate and should not be the primary/clock device. Use Built-in Output or BlackHole 2ch as the clock device instead

### 1.4 Audio Capture Engine

- **Auto-detect capture device**: The `auto_select_capture_device()` function prefers MeetingCapture aggregate devices, tests each candidate by opening a brief stream to verify it is online, and skips offline devices (e.g., disconnected AirPods). Available via API endpoint `GET /api/auto-detect-device`. The record page auto-selects the best device on load with an "auto-detected" indicator.
- **Native sample rate detection**: The capture engine queries the selected audio device for its native/default sample rate and uses that rate for recording. This avoids resampling artifacts and PortAudio errors from unsupported rates.
- Real-time audio capture using the device's native sample rate
- Circular buffer to prevent data loss during processing spikes, protected by `_frames_lock` against concurrent access
- **Logarithmic audio level calculation**: Audio levels are computed using RMS with logarithmic (dB) scaling, normalized to a 0.0-1.0 range for display in the web UI.
- VAD (Voice Activity Detection) to detect when speech starts/stops
- Continuous recording with configurable silence-based auto-stop (e.g., stop after 5 minutes of silence)
- **Auto-save**: Audio is saved every 5 minutes during recording as a recovery file, protecting against crashes or unexpected shutdowns.
- **Multi-channel capture**: Opens all channels on aggregate devices and mixes to mono for transcription.
- **Device fallback**: On macOS audio errors (e.g., device disconnected), retries with the default system device.
- **Explicit blocksize**: Uses `blocksize=1024` for predictable callback timing.
- **Safe shutdown**: Uses `stream.stop()` instead of `stream.abort()` for safe audio stream shutdown.
- **PortAudio device re-scan**: When a device is not found or becomes unavailable, the engine re-scans audio devices by calling `sd._terminate()` followed by `sd._initialize()` to detect newly connected devices without restarting the application. Re-scan only runs when not actively recording.

### 1.5 Recording Controls

- **Start/Stop**: Manual trigger via system tray icon, global hotkey, or CLI command
- **Pause/Resume**: Temporarily pause capture without ending the session
- **Auto-start**: Option to automatically begin recording when a supported meeting app becomes active (detect via process monitoring or calendar integration)
- **Auto-stop**: End recording when meeting app closes or audio silence exceeds threshold

### 1.6 Live Note-Taking

During an active recording, users can provide additional context via the web UI:

- **Speaker names**: Comma-separated list of participant names to assist with diarization speaker mapping
- **Notes**: Free-form text notes taken during the meeting, included as context for minutes generation
- **Custom LLM instructions**: Specific instructions for the LLM when generating minutes (e.g., "focus on budget discussions", "use bullet points only")

Notes are saved to `data/notes/{meeting_id}.json` and automatically loaded by the pipeline during minutes generation. The notes file contains:

```json
{
  "meeting_id": "uuid",
  "speaker_names": ["Alice", "Bob"],
  "notes": "Discussed Q3 budget...",
  "custom_instructions": "Focus on action items for the engineering team"
}
```

---

## 2. Transcription Engine

### 2.1 Transcription Backends

The transcription system uses a **factory pattern** (`get_transcription_engine()`) that creates the appropriate engine based on `config.transcription.primary_engine`. All engines implement the `BaseTranscriptionEngine` abstract class with `transcribe()` and `detect_language()` methods.

#### Engine A: Faster Whisper (Default — `primary_engine: whisper`)

- **Library**: `faster-whisper` (CTranslate2 backend)
- **Model sizes**: tiny, base, small, medium, large-v3 (user-configurable, default: medium for balance of speed/accuracy)
- **Distil-Whisper models**: `distil-medium.en`, `distil-large-v3` — faster variants optimized for English, providing significant speedups with minimal accuracy loss
- **Language**: Auto-detect or user-specified; support for multilingual meetings
- **Word-level timestamps**: Full word-level timestamps and confidence scores
- **Hardware acceleration**: CUDA (NVIDIA), Metal (Apple Silicon with automatic detection and fallback to CPU), CPU fallback with int8 quantization
- **Processing**: Post-meeting batch transcription for high accuracy

#### Engine B: Whisper.cpp (GGML — `primary_engine: whisper-cpp`)

- **Library**: `pywhispercpp` (whisper.cpp C++ backend with GGML quantization)
- **Model sizes**: Same Whisper models but quantized to Q4/Q5 for lower memory
- **Advantages**: 2-3x faster than faster-whisper on CPU-only machines, ~50% less RAM
- **Trade-off**: No word-level timestamps by default, slightly lower accuracy
- **Best for**: CPU-only machines or memory-constrained environments
- **Install**: `pip install pywhispercpp` or `pip install -e ".[local-ai]"`

#### Model Presets

| Preset | Model | Use Case |
|--------|-------|----------|
| `fast` | `distil-medium.en` | Quick English transcription |
| `balanced` | `medium` | Good accuracy, reasonable speed |
| `best` | `large-v3` | Highest accuracy, needs 10GB+ RAM/VRAM |

#### Hardware Auto-Detection

The system auto-detects available hardware and selects optimal settings:

| Hardware | Device | Compute Type | Recommended Model |
|----------|--------|-------------|-------------------|
| Apple Silicon (M1-M4) | Metal (`auto`) | float16 | large-v3 |
| NVIDIA GPU (6GB+ VRAM) | CUDA | float16 | large-v3 |
| NVIDIA GPU (<6GB VRAM) | CUDA | float16 | medium |
| CPU only (16GB+ RAM) | CPU | int8 | medium |
| CPU only (<16GB RAM) | CPU | int8 | small or base |

Hardware profile and recommendations available via `GET /api/config/hardware`.

### 2.2 Speaker Diarization

- Identify and label distinct speakers in the audio
- **Local**: `pyannote.audio` speaker diarization pipeline (uses `token=` parameter for HuggingFace authentication, not the deprecated `use_auth_token`)
- Map speaker labels to known participants (see metadata enrichment)
- Support for pre-registering speaker voice profiles for improved identification

### 2.3 Transcription Quality

- Word-level timestamps for precise alignment
- Confidence scores per word/segment
- Punctuation restoration and capitalization
- Profanity filtering (optional, configurable)
- Custom vocabulary/glossary support (company names, technical terms, acronyms)

---

## 3. Metadata Extraction & Enrichment

### 3.1 Automatic Metadata

| Field | Source | Description |
|-------|--------|-------------|
| `meeting_id` | Generated | UUID for unique identification |
| `timestamp_start` | Audio capture | ISO 8601 datetime when recording began |
| `timestamp_end` | Audio capture | ISO 8601 datetime when recording ended |
| `duration_seconds` | Computed | Total meeting duration |
| `audio_file_path` | Storage | Path to original audio file |
| `recording_device` | System | Audio device used for capture |
| `sample_rate` | Audio capture | Audio sample rate |
| `platform` | Process detection | Detected meeting platform (Zoom, Teams, etc.) |
| `transcription_engine` | Config | Which engine produced the transcript |
| `transcription_model` | Config | Model name/version used |
| `language` | Transcription | Detected or specified language |
| `num_speakers` | Diarization | Number of distinct speakers detected |

### 3.2 Calendar Integration Metadata

- Query Google Calendar / Outlook Calendar for meetings overlapping the recording time window
- Extract:
  - `calendar_event_title`
  - `calendar_event_description`
  - `organizer`
  - `attendees` (list of names + emails)
  - `meeting_link` (Zoom/Teams URL)
  - `recurrence` (is this a recurring meeting? which instance?)
  - `calendar_labels` / `categories`

### 3.3 Speaker Mapping

- Match diarized speaker labels (Speaker 1, Speaker 2, ...) to calendar attendees
- Use voice profile matching if available
- Allow manual correction via a review UI
- Store mapping as `speakers` array with `label`, `name`, `email`, `confidence`

### 3.4 Meeting Type Classification

- Auto-classify meeting type based on calendar metadata + transcript content:
  - `customer_meeting` / `client_call`
  - `one_on_one_direct_report` (1:1 with an employee / direct report)
  - `one_on_one_leader` (1:1 with your boss / leader)
  - `standup` / `daily_sync`
  - `team_meeting` (decisions, financial review, blockers, strategic updates)
  - `interview`
  - `brainstorm`
  - `decision_meeting`
  - `presentation` / `demo`
  - `all_hands`
  - `retrospective`
  - `planning` / `sprint_planning`
  - `workshop`
  - `other`
- Classification uses: number of attendees, meeting title patterns, recurrence patterns, transcript content analysis
- User can override classification before minutes generation

---

## 4. Output Format

### 4.1 Transcript JSON Schema

```json
{
  "schema_version": "1.0",
  "meeting_id": "uuid",
  "metadata": {
    "timestamp_start": "2026-03-28T10:00:00Z",
    "timestamp_end": "2026-03-28T10:45:00Z",
    "duration_seconds": 2700,
    "platform": "zoom",
    "language": "en-US",
    "transcription_engine": "whisper",
    "transcription_model": "large-v3",
    "audio_file": "recordings/2026-03-28_standup.wav",
    "recording_device": "MeetingCapture (Aggregate: Built-in Microphone + BlackHole 2ch)"
  },
  "calendar": {
    "event_id": "google-calendar-event-id",
    "title": "Daily Standup",
    "organizer": { "name": "Alice", "email": "alice@company.com" },
    "attendees": [
      { "name": "Alice", "email": "alice@company.com" },
      { "name": "Bob", "email": "bob@company.com" }
    ],
    "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "labels": ["engineering"]
  },
  "speakers": [
    { "label": "SPEAKER_00", "name": "Alice", "email": "alice@company.com", "confidence": 0.92 },
    { "label": "SPEAKER_01", "name": "Bob", "email": "bob@company.com", "confidence": 0.87 }
  ],
  "meeting_type": "standup",
  "meeting_type_confidence": 0.95,
  "transcript": {
    "segments": [
      {
        "id": 0,
        "start": 0.0,
        "end": 4.5,
        "speaker": "SPEAKER_00",
        "text": "Good morning everyone, let's get started with the standup.",
        "words": [
          { "word": "Good", "start": 0.0, "end": 0.3, "confidence": 0.98 },
          { "word": "morning", "start": 0.3, "end": 0.7, "confidence": 0.97 }
        ]
      }
    ],
    "full_text": "Good morning everyone, let's get started with the standup. ..."
  },
  "processing": {
    "created_at": "2026-03-28T10:46:00Z",
    "processing_time_seconds": 45,
    "pipeline_version": "1.0.0"
  }
}
```

### 4.2 Audio File Storage

- Store original audio as lossless FLAC (archival) or compressed OGG/Opus (space-efficient)
- Configurable retention policy (e.g., delete audio after 90 days, keep transcript indefinitely)
- Optional: encrypt audio files at rest

---

## 5. Configuration

### 5.1 User Preferences

Stored in `config/config.yaml` (YAML format). Fallback: `~/.meeting-minutes/config.yaml`.

```json
{
  "recording": {
    "audio_device": "MeetingCapture",
    "sample_rate": 16000,
    "format": "flac",
    "auto_start": true,
    "auto_stop_silence_minutes": 5,
    "hotkey_start": "Ctrl+Shift+R",
    "hotkey_stop": "Ctrl+Shift+S"
  },
  "transcription": {
    "primary_engine": "whisper",
    "whisper_model": "medium",
    "language": "auto",
    "realtime": true,
    "batch_reprocess": true,
    "custom_vocabulary": [
      "Kubernetes",
      "NextJS",
      "CompanyName"
    ]
  },
  "diarization": {
    "enabled": true,
    "engine": "pyannote",
    "max_speakers": 10,
    "voice_profiles_dir": "~/.meeting-minutes/voice-profiles/"
  },
  "calendar": {
    "provider": "google",
    "auto_match": true,
    "match_window_minutes": 15
  },
  "storage": {
    "recordings_dir": "~/MeetingRecordings/",
    "transcripts_dir": "~/MeetingTranscripts/",
    "audio_retention_days": 90,
    "encrypt_audio": false
  }
}
```

---

## 6. Privacy & Compliance

- **Local-first**: All audio processing can happen entirely on-device (Whisper + pyannote)
- **Consent**: Display a visible recording indicator (system tray icon, notification)
- **Data residency**: When using cloud transcription, respect data residency requirements
- **Redaction**: Option to auto-redact PII (names, emails, phone numbers, SSNs) from transcripts
- **Encryption**: Optional at-rest encryption for audio files and transcripts
- **Retention**: Configurable auto-deletion of audio files after specified period

---

## 7. System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 4 cores | 8+ cores (Apple Silicon / modern x86) |
| RAM | 8 GB | 16 GB |
| GPU | None (CPU mode) | NVIDIA GPU with 6GB+ VRAM or Apple Silicon |
| Storage | 1 GB per hour of audio | SSD recommended |
| OS | macOS 12+, Windows 10+, Ubuntu 22.04+ | macOS 14+ (Apple Silicon) |

---

## 8. Tech Stack

| Component | Technology |
|-----------|-----------|
| Audio capture | `sounddevice` (Python) or platform-specific APIs |
| Virtual audio | BlackHole (macOS), WASAPI (Windows), PipeWire (Linux) |
| VAD | Silero VAD or WebRTC VAD |
| Local transcription | `faster-whisper` / `whisper.cpp` |
| Cloud transcription | AWS Transcribe SDK (`boto3`) |
| Speaker diarization | `pyannote.audio` |
| Calendar integration | Google Calendar API / Microsoft Graph API |
| Configuration | JSON config files |
| CLI interface | `typer` or `click` |
| System tray | `pystray` or Electron-based UI |
