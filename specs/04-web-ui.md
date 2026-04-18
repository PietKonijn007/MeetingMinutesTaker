# System 4: Web UI

## Overview

A visually polished, browser-based single-page application. In production, everything (API + UI) is served from `localhost:8080` by FastAPI. In development, the Svelte dev server runs at `localhost:3000` and proxies API requests to `:8080`. Built with Svelte + Vite on the frontend and FastAPI on the backend. The UI provides an intuitive way to browse, search, and manage meetings without touching the CLI.

---

## 1. Design Principles

### 1.1 Visual Identity

- **Clean, warm, professional** — think Linear meets Notion. Not sterile or clinical.
- **Generous whitespace** — content breathes. No cramped tables or walls of text.
- **Soft color palette** — muted backgrounds, accent colors for interactive elements, never garish.
- **Typography-first** — readable body text (16px Inter/System), clear heading hierarchy, monospace only for IDs and timestamps.
- **Subtle motion** — transitions on route changes (150-200ms fade), hover states, skeleton loaders instead of spinners.

### 1.2 Color System

| Token | Light Mode | Dark Mode | Usage |
|-------|-----------|-----------|-------|
| `bg-primary` | `#FAFAFA` | `#111113` | Page background |
| `bg-surface` | `#FFFFFF` | `#1A1A1D` | Cards, panels, modals |
| `bg-surface-hover` | `#F5F5F5` | `#222225` | Hover state on surfaces |
| `border-subtle` | `#E8E8EC` | `#2E2E32` | Dividers, card borders |
| `text-primary` | `#1A1A1A` | `#ECECEC` | Headings, body text |
| `text-secondary` | `#6B6B76` | `#8B8B96` | Metadata, timestamps, labels |
| `text-muted` | `#9B9BA5` | `#5B5B65` | Placeholders, disabled |
| `accent` | `#6366F1` | `#818CF8` | Links, active states, primary buttons |
| `accent-hover` | `#4F46E5` | `#6366F1` | Hover on accent elements |
| `success` | `#22C55E` | `#4ADE80` | Completed actions, positive indicators |
| `warning` | `#F59E0B` | `#FBBF24` | Overdue items, alerts |
| `danger` | `#EF4444` | `#F87171` | Destructive actions, errors |

### 1.3 Dark Mode

- Toggle in the top-right corner (sun/moon icon)
- Follows system preference on first visit (`prefers-color-scheme`)
- Preference persisted in `localStorage`
- All color tokens swap via CSS custom properties on `<html data-theme="dark">`

### 1.4 Responsive Layout

| Breakpoint | Name | Layout |
|------------|------|--------|
| < 640px | Mobile | Single column, collapsible sidebar, bottom nav |
| 640-1024px | Tablet | Sidebar collapsed by default, two-column content |
| > 1024px | Desktop | Persistent sidebar, three-column where applicable |

---

## 2. Layout & Navigation

### 2.1 Shell Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  ┌─────────┐   Meeting Minutes Taker          🔍  ☀️  ⚙️       │
│  │ Logo    │                                                    │
├──┴─────────┴────────────────────────────────────────────────────┤
│  │           │                                                  │
│  │  Sidebar  │               Main Content Area                  │
│  │           │                                                  │
│  │  📋 Meetings │                                               │
│  │  ✅ Actions   │                                               │
│  │  📌 Decisions │                                               │
│  │  👤 People    │                                               │
│  │  📊 Stats     │                                               │
│  │           │                                                  │
│  │  ──────── │                                                  │
│  │  ⏺ Record │                                                  │
│  │  ⚙ Settings│                                                  │
│  │           │                                                  │
└──┴───────────┴──────────────────────────────────────────────────┘
```

### 2.2 Sidebar Navigation

The sidebar is the primary navigation. It is always visible on desktop (240px wide), collapsible on tablet/mobile.

| Item | Icon | Route | Description |
|------|------|-------|-------------|
| **Meetings** | `📋` | `/` | Default view. Meeting list and search. |
| **Action Items** | `✅` | `/actions` | Cross-meeting action item tracker |
| **Decisions** | `📌` | `/decisions` | Decision log across all meetings |
| **People** | `👤` | `/people` | People directory with meeting history |
| **Stats** | `📊` | `/stats` | Meeting analytics and charts |
| — separator — | | | |
| **Record** | `⏺` | `/record` | Recording controls |
| **Settings** | `⚙` | `/settings` | Configuration editor |

Active item has accent-colored left border and tinted background.

### 2.3 Top Bar

- **Left**: App logo/icon + "Meeting Minutes" title (clickable → home)
- **Center**: Global search input (always visible on desktop, icon-only on mobile that expands)
- **Right**: Dark mode toggle, settings gear, recording status indicator (pulsing red dot when active)

---

## 3. Pages & Views

### 3.1 Meetings Page (`/`)

The default landing page. Shows all meetings with powerful filtering.

#### 3.1.1 View Modes

Three view modes, toggleable via segmented control in the top-right:

**List View** (default):

```
┌─────────────────────────────────────────────────────────────┐
│  🔍 Search meetings...          [Type ▾] [Date ▾] [≡ ▦ 📅]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Daily Standup                          Mar 28, 2026│   │
│  │  standup · 15 min · Alice, Bob, Carol               │   │
│  │  Sprint velocity discussed. Bob blocked on API...   │   │
│  │  ✅ 2 actions · 📌 1 decision                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Q2 Pricing Decision                   Mar 27, 2026│   │
│  │  decision_meeting · 45 min · Alice, Dave, Eve       │   │
│  │  Decided to adopt tiered pricing model...           │   │
│  │  ✅ 4 actions · 📌 3 decisions                      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  1:1 with Bob                          Mar 26, 2026│   │
│  │  one_on_one · 30 min · Alice, Bob                   │   │
│  │  Career growth discussion. Bob interested in...     │   │
│  │  ✅ 1 action                                        │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│                     Load More ↓                             │
└─────────────────────────────────────────────────────────────┘
```

Each meeting card shows:
- **Title** (bold, large)
- **Date** (right-aligned, secondary text)
- **Metadata row**: meeting type badge (colored pill), duration, attendee names (truncated with `+N more`)
- **Summary snippet**: first 1-2 lines of the summary, faded overflow
- **Counts row**: action item count, decision count — clickable to jump to those sections
- **Hover**: subtle lift shadow, entire card is clickable

**Grid View**:

Same data, displayed as a 2-3 column card grid. Better for visual scanning. Cards are taller, show a colored strip on top based on meeting type.

**Calendar View**:

Month calendar with dots/pills on days that have meetings. Clicking a day shows that day's meetings in a side panel. Meeting type indicated by dot color.

#### 3.1.2 Filters & Search

- **Search bar**: Full-text search with debounced input (300ms). Shows results inline, replacing the list.
- **Type filter**: Dropdown multi-select with colored meeting type badges
- **Date filter**: Date range picker (presets: Today, This Week, This Month, This Quarter, Custom)
- **Person filter**: Searchable dropdown of known attendees
- **Active filters** shown as dismissible chips below the search bar

#### 3.1.3 Empty States

When no meetings exist yet:

```
┌─────────────────────────────────────────────────┐
│                                                 │
│            📋                                   │
│     No meetings yet                            │
│                                                 │
│  Record your first meeting to get started.     │
│                                                 │
│        [ Start Recording ]                     │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

### 3.2 Meeting Detail Page (`/meeting/:id`)

The richest page. Shows everything about a single meeting.

#### 3.2.1 Layout

```
┌─────────────────────────────────────────────────────────────┐
│  ← Back to Meetings                                        │
│                                                             │
│  Daily Standup                                              │
│  ┌──────┐ ┌────────┐ ┌──────────┐ ┌──────────┐            │
│  │standup│ │15 min  │ │Mar 28    │ │3 people  │            │
│  └──────┘ └────────┘ └──────────┘ └──────────┘            │
│  Organizer: Alice · Attendees: Alice, Bob, Carol            │
│                                                             │
│  ┌─────────┬──────────┬────────────┬────────────┐          │
│  │ Minutes │Transcript│ Actions (2)│ Decisions(1)│          │
│  ├─────────┴──────────┴────────────┴────────────┤          │
│  │                                               │          │
│  │  ## Summary                                   │          │
│  │  Sprint velocity discussed. Team agreed to...│          │
│  │                                               │          │
│  │  ## Alice                                     │          │
│  │  - **Done**: Completed the API refactor...   │          │
│  │  - **Today**: Working on auth flow...        │          │
│  │  - **Blockers**: None                        │          │
│  │                                               │          │
│  │  ## Bob                                       │          │
│  │  - **Done**: Fixed the CI pipeline...        │          │
│  │  - **Today**: Database migration script...   │          │
│  │  - **Blockers**: Waiting on API spec from... │          │
│  │                                               │          │
│  │  ## Action Items                              │          │
│  │  ☐ Review API spec — Bob (Due: Mar 29)       │          │
│  │  ☐ Update sprint board — Alice               │          │
│  │                                               │          │
│  └───────────────────────────────────────────────┘          │
│                                                             │
│  ┌───────────────────────────────────┐                     │
│  │ 🏷 Tags: engineering, sprint-14   │  [+ Add tag]       │
│  └───────────────────────────────────┘                     │
│                                                             │
│  [Regenerate] [Export ▾] [Delete]                           │
└─────────────────────────────────────────────────────────────┘
```

#### 3.2.2 Tabs

| Tab | Content |
|-----|---------|
| **Minutes** (default) | Rendered markdown of the generated minutes. Clean typography. |
| **Transcript** | Full transcript with speaker labels and timestamps. Optionally with audio player synced to timestamps. |
| **Actions** | Action items from this meeting with status toggles (checkbox to mark done). Badge shows count. |
| **Decisions** | Decisions made in this meeting. Each with description and who made it. |

#### 3.2.3 Header Section

- **Title**: Large heading
- **Metadata pills**: Meeting type (colored badge), duration, date, attendee count — in a horizontal row
- **Attendees**: Expandable. Shows first 5 names, `+N more` to expand.
- **Tags**: Inline editable. Click `+` to add, click `×` on tag to remove.

#### 3.2.4 Minutes Tab

Structured card layout (replaces the old flat markdown render):

- **Summary card**: accent left-border, sentiment badge in the corner, summary text. Always present.
- **Key topics chips**: hashtag-style chips from `key_topics`.
- **Discussion section**: collapsible cards for each item in `discussion_points`. Header bar shows topic title, sentiment badge, and participant avatars (up to 3 + overflow). Click to expand for full summary and complete participant list. "Expand all / Collapse all" toggle above the cards.
- **Outcomes grid** (2 columns): Decisions (◆) and Action items (○) preview. Each shows the top 3 with "View all →" deep-link to the respective tab.
- **Risks & Concerns**: yellow left-border card with ⚠ icon per item.
- **Follow-ups**: card with owner/timeframe badges per item.
- **Parking lot**: dashed-border card.
- **Sections fallback**: when `discussion_points` is empty but `sections[]` (from text+regex path) has content, renders sections as collapsible cards identical to Discussion. Deduplicates sections whose headings match already-rendered cards.
- **Raw markdown toggle**: button in the top-right switches to the original markdown blob for anyone who wants the flat view.

Card data comes from the new fields in `MinutesResponse`: `sentiment`, `discussion_points`, `risks_and_concerns`, `follow_ups`, `parking_lot`, `key_topics`, `sections`. Fields are populated from `minutes.structured_json` in the DB or falls back to reading the on-disk minutes JSON file.

#### 3.2.5 Transcript Tab

```
┌─────────────────────────────────────────────────────────────┐
│  ▶ 00:00 ─────────●───────────────────── 14:32  🔊         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  00:00  Alice                                               │
│  Good morning everyone, let's get started with the standup. │
│                                                             │
│  00:05  Bob                                                 │
│  Hey, so yesterday I finished the CI pipeline fix...        │
│                                                             │
│  00:42  Carol                                               │
│  I've been working on the onboarding flow...                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

- **Audio player**: Minimal, at the top. Play/pause, seek bar, current time, volume. Only shown if audio file exists.
- **Speaker legend bar**: above the segments, shows each unique speaker with a color dot (up to 8 cycling colors). Speaker labels (`SPEAKER_00`) are resolved to real names via the `speakers[].name` mapping in the transcript JSON.
- **Transcript segments**: Each segment shows timestamp (clickable — seeks audio), speaker name with a colored dot matching the legend, and text.
- **Active segment highlighting**: The currently playing segment has a tinted background that follows playback.
- **"✎ Name speakers" button** (top-right of legend bar): when any generic `SPEAKER_XX` labels are present, this button opens an inline editor with one text input per unique speaker. Saving calls `PATCH /api/meetings/:id/transcript/speakers` which rewrites segments and the speakers array in the transcript JSON, updates `data/notes/{id}.json` so reprocess/regenerate use the names, and optionally regenerates minutes with the new names. Use "Save only" to update transcript without regenerating.
- **Click-to-seek**: Click any timestamp to jump audio to that point.

#### 3.2.6 Action Bar

Bottom of the page, sticky on scroll:

| Button | Style | Action |
|--------|-------|--------|
| **Regenerate** | Ghost/outlined | Re-run LLM generation with current transcript |
| **Export** | Ghost/outlined, dropdown | Export as Markdown, PDF, or Google Doc |
| **Delete** | Ghost/outlined, danger color | Confirmation modal, then delete all data |

---

### 3.3 Action Items Page (`/actions`)

A Kanban-inspired board or filterable list of all action items across meetings.

#### 3.3.1 Default: List View

```
┌─────────────────────────────────────────────────────────────┐
│  Action Items                       [Owner ▾] [Status ▾]   │
│                                                             │
│  ☐  Review API spec                                        │
│     Bob · Due Mar 29 · from Daily Standup (Mar 28)         │
│                                                             │
│  ☐  Update sprint board                                    │
│     Alice · No due date · from Daily Standup (Mar 28)      │
│                                                             │
│  ☐  Send revised pricing proposal to Acme                  │
│     Dave · Due Apr 1 · from Q2 Pricing Decision (Mar 27)   │
│                                                             │
│  ──── Completed ────────────────────────────────            │
│                                                             │
│  ☑  Fix CI pipeline                                        │
│     Bob · Completed Mar 28 · from Sprint Planning (Mar 25) │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

- Checkbox toggles status between open/done (instant, optimistic update)
- Each item links to its source meeting
- Overdue items have a warning badge
- Grouped: Open items first, completed collapsed at bottom

#### 3.3.2 Filters

- **Owner**: Dropdown of all people with assigned actions
- **Status**: Open / In Progress / Done / All
- **Due date**: Overdue / Due This Week / Due This Month / All
- **Meeting**: Filter to a specific meeting's actions

---

### 3.4 Decisions Page (`/decisions`)

Chronological log of all decisions across meetings.

```
┌─────────────────────────────────────────────────────────────┐
│  Decision Log                              🔍 Search...    │
│                                                             │
│  Mar 27, 2026                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Adopt tiered pricing model for Q2                  │   │
│  │  Made by: Alice · Q2 Pricing Decision               │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Delay EU launch to Q3                              │   │
│  │  Made by: Dave · Q2 Pricing Decision                │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Mar 25, 2026                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Use PostgreSQL for the new service                 │   │
│  │  Made by: Bob · Sprint Planning                     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

- Grouped by date
- Each decision links to source meeting
- Searchable

---

### 3.5 People Page (`/people`)

Directory of all people who have appeared in meetings.

```
┌─────────────────────────────────────────────────────────────┐
│  People                                 🔍 Search...       │
│                                                             │
│  ┌────┐  Alice (alice@company.com)                         │
│  │ AW │  12 meetings · 3 open actions · Last: Mar 28      │
│  └────┘                                                    │
│                                                             │
│  ┌────┐  Bob (bob@company.com)                             │
│  │ BJ │  10 meetings · 2 open actions · Last: Mar 28      │
│  └────┘                                                    │
│                                                             │
│  ┌────┐  Carol (carol@company.com)                         │
│  │ CS │  8 meetings · 0 open actions · Last: Mar 28       │
│  └────┘                                                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

- Avatar with initials (colored by hash of name)
- Clicking a person shows their meeting history, action items, and decisions
- Sort by meeting count, name, or last seen

#### Person Detail (`/people/:id`)

- Meeting history (list of meetings this person attended, reverse chronological)
- Open action items assigned to them
- Decisions they made
- Stats: total meetings, avg meeting duration, most common meeting types

**Edit / Delete / Merge actions** (top-right of the header):

- **✎ Edit** — toggles inline inputs for name and email. Save calls `PATCH /api/people/:id`. Renaming automatically propagates the new name to `action_items.owner` and `decisions.made_by` so historical attributions stay consistent. 409 returned if the email is already used by another person (use Merge instead).
- **Merge…** — opens a modal with a dropdown of all other people (sorted by name, showing meeting count). Target is the person to keep. Checkbox (default on) controls whether historical `owner`/`made_by` references to the source name are rewritten to the target's name. Calls `POST /api/people/:id/merge {target_id, rename_actions}`. After merge, user is redirected to the target's detail page. The source person's email is carried over if the target had none.
- **Delete** — shows a ConfirmModal explaining that historical attributions are preserved; on confirm, calls `DELETE /api/people/:id`. Person is removed from `meeting_attendees` rows; historical `owner`/`made_by` strings are left alone (they're strings, not FKs).

---

### 3.6 Stats Page (`/stats`)

Dashboard with meeting analytics. Uses lightweight charting (Chart.js or uPlot).

#### 3.6.1 Summary Cards (top row)

```
┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
│   Total    │  │  This Week │  │ Open Actions│  │  Avg Dur.  │
│    47      │  │     5      │  │     8       │  │   28 min   │
│  meetings  │  │  meetings  │  │   items     │  │            │
└────────────┘  └────────────┘  └────────────┘  └────────────┘
```

#### 3.6.2 Charts

| Chart | Type | Data |
|-------|------|------|
| **Meetings over time** | Area chart | Meetings per week over the last 3 months |
| **By type** | Donut chart | Distribution of meeting types |
| **Time in meetings** | Bar chart | Hours per week spent in meetings |
| **Action item velocity** | Line chart | Created vs. completed action items per week |
| **Top attendees** | Horizontal bar | People with most meetings this month |

Charts should use the accent color palette, be interactive (hover for tooltips), and respect dark mode.

---

### 3.7 Record Page (`/record`)

Controls for starting/stopping recording. Shows live status.

#### 3.7.1 Idle State

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                        ⏺                                    │
│                  Start Recording                            │
│                                                             │
│   Audio Device: MeetingCapture                    [Change]  │
│   Whisper Model: medium                                     │
│   Pipeline: automatic                                       │
│                                                             │
│   Recent recordings:                                        │
│   · Daily Standup — Mar 28, 15:00 — ✅ Processed           │
│   · Q2 Pricing — Mar 27, 14:00 — ✅ Processed              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 3.7.2 Recording State

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                    🔴 Recording...                          │
│                      04:32                                  │
│                                                             │
│   ▁▂▃▅▃▂▁▂▃▅▇▅▃▂▁▂▃▅▃▂   (live audio waveform)          │
│                                                             │
│   Meeting ID: a1b2c3d4-...                                 │
│   Audio Device: MeetingCapture                              │
│                                                             │
│              [ ⏸ Pause ]  [ ⏹ Stop ]                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

- Pulsing red dot animation
- Live elapsed time counter
- Audio level visualizer (waveform or VU meter) using logarithmic dB-scaled levels
- The top bar also shows a small pulsing red dot when recording is active (visible from any page)
- Recording and pipeline status updates are delivered in real time via WebSocket push (replaced HTTP polling). Each pipeline job is tracked with step/progress/error and auto-cleans up after 60 seconds.
- **Auto-detect capture device**: On page load, calls `GET /api/auto-detect-device` which prefers MeetingCapture aggregate devices, tests each candidate by opening a brief stream to verify it is online, and skips offline devices. Shows an "auto-detected" indicator next to the device name.
- Audio device selection uses native sample rate detection (queries device for its default rate)
- PortAudio re-scan (`sd._terminate()` + `sd._initialize()`) is used to detect newly connected audio devices without restarting

#### 3.7.3 Processing State

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Processing meeting a1b2c3d4...                           │
│                                                             │
│   ✅  Audio saved (recordings/a1b2c3d4.flac)               │
│   ⏳  Transcribing... (42%)                                │
│   ○   Generating minutes                                   │
│   ○   Indexing                                             │
│                                                             │
│              [ View when ready ]                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

- Step-by-step progress indicator
- Each step: pending (○), in progress (⏳ with optional %), complete (✅), failed (❌)
- On completion, link to the meeting detail page

---

### 3.8 Settings Page (`/settings`)

Visual config editor. Changes write to `config/config.yaml`.

#### 3.8.1 Sections

| Section | Settings |
|---------|----------|
| **Recording** | Audio device (dropdown of system devices), sample rate, auto-stop silence threshold |
| **Transcription** | Engine selector (Faster Whisper / Whisper.cpp) with install status badges, Whisper model (dropdown with size/accuracy descriptions + hardware-recommended model hint), language |
| **Speaker ID** | Enable/disable diarization, HuggingFace token status |
| **Performance & Hardware** | MPS CPU fallback toggle (Apple Silicon) — sets `PYTORCH_ENABLE_MPS_FALLBACK=1`. Requires service restart to take effect. Persists to `performance.pytorch_mps_fallback` in config.yaml and is applied at startup via `AppConfig.model_post_init()`. |
| **Minutes Generation** | LLM provider (Anthropic, OpenAI, OpenRouter, Ollama), model (dynamic dropdown fetched from provider APIs — including Ollama local models with size/family info), Ollama status indicator (running/version/not installed), hardware-aware model recommendations, custom model text input, temperature slider, max tokens |
| **Pipeline** | Mode selector (automatic / semi-automatic / manual) |
| **Storage** | Database path, data directory |
| **Appearance** | Dark mode toggle, accent color (optional) |

**Local AI features in Settings:**
- **Transcription engine selector**: Dropdown to choose between Faster Whisper (GPU-accelerated, best accuracy) and Whisper.cpp (lower memory, faster on CPU). Install status badges show which engines are available.
- **Hardware detection**: `GET /api/config/hardware` detects GPU type, VRAM, RAM and recommends optimal Whisper model and Ollama models. Recommendations shown inline in the settings UI.
- **Ollama integration**: When Ollama is selected as provider, the model dropdown is populated by querying the local Ollama instance (`GET /api/config/provider-models?provider=ollama`). Models show parameter size and disk size. An Ollama status badge shows whether the server is running and its version.
- **Model refresh**: All providers (including Ollama) support a Refresh button to re-fetch available models.

Each setting has a label, description, and appropriate input control (dropdown, slider, toggle, text input). Changes are saved on blur or via a "Save" button at the bottom.

---

## 4. Components Library

### 4.1 Core Components

| Component | Description |
|-----------|-------------|
| `MeetingCard` | Clickable card showing meeting summary. Used in list/grid views. |
| `MeetingTypeBadge` | Colored pill showing meeting type (e.g., green for standup, blue for decision). |
| `ActionItemRow` | Checkbox + description + owner + due date. Toggle updates DB. |
| `DecisionCard` | Decision description with maker and source meeting link. |
| `PersonAvatar` | Circle with initials, color derived from name hash. |
| `SearchBar` | Debounced input with filter chips, command palette shortcut (`Cmd+K`). |
| `AudioPlayer` | Minimal play/pause/seek/volume. Syncs with transcript segments. |
| `MarkdownRenderer` | Renders minutes markdown with proper styling. Interactive checkboxes. |
| `TagEditor` | Inline tag list with add/remove. Autocompletes from existing tags. |
| `DateRangePicker` | Two-date picker with presets (Today, This Week, etc.). |
| `EmptyState` | Centered icon + message + CTA button for empty pages. |
| `Skeleton` | Content placeholder animation (shimmer) for loading states. |
| `ConfirmModal` | Confirmation dialog for destructive actions (delete). |
| `Toast` | Bottom-right notification for success/error feedback. |
| `StatusStepper` | Vertical step list showing pipeline progress (recording page). |

### 4.2 Meeting Type Colors

| Type | Color | Hex |
|------|-------|-----|
| `standup` | Green | `#22C55E` |
| `one_on_one` | Sky | `#0EA5E9` |
| `customer_meeting` | Purple | `#A855F7` |
| `team_meeting` | Indigo | `#6366F1` |
| `decision_meeting` | Amber | `#F59E0B` |
| `brainstorm` | Pink | `#EC4899` |
| `retrospective` | Orange | `#F97316` |
| `planning` | Teal | `#14B8A6` |
| `other` | Gray | `#6B7280` |

---

## 5. REST API (Backend)

The web UI is powered by a FastAPI backend. All state changes go through the API; the UI never touches SQLite directly.

### 5.1 Endpoints

```
GET    /api/meetings                         # List meetings (paginated, filtered)
       ?q=<search>&type=<type>&after=<date>&before=<date>&person=<email>
       &limit=20&offset=0
GET    /api/meetings/:id                     # Meeting detail (includes minutes, actions, decisions, discussion_points, risks_and_concerns, follow_ups, parking_lot, key_topics, sections)
GET    /api/meetings/:id/transcript          # Full transcript with segments (id, start, end, speaker, text) + speakers mapping
PATCH  /api/meetings/:id/transcript/speakers # Rename speakers. Body: {"mapping": {"SPEAKER_00": "Tom"}} or {"ordered_names": ["Tom", "Mary"]}
GET    /api/meetings/:id/audio               # Stream audio file
PATCH  /api/meetings/:id                     # Update tags, status
DELETE /api/meetings/:id                     # Delete meeting + all data

GET    /api/search?q=<query>                 # Full-text search
       &type=<type>&after=<date>&before=<date>&limit=20

GET    /api/action-items                     # All action items (filtered)
       ?owner=<email>&status=<open|done|all>&overdue=true
PATCH  /api/action-items/:id                 # Update status
       { "status": "done" }

GET    /api/decisions                        # All decisions (filtered, paginated)
       ?after=<date>&before=<date>&limit=50

GET    /api/people                           # All known people
GET    /api/people/:id                       # Person detail
GET    /api/people/:id/meetings              # Meetings for a person
GET    /api/people/:id/action-items          # Action items for a person
PATCH  /api/people/:id                       # Rename (propagates to action_items.owner + decisions.made_by) and/or change email
DELETE /api/people/:id                       # Delete person (removes from meeting_attendees, keeps historical owner/made_by strings)
POST   /api/people/:id/merge                 # Merge into another. Body: {"target_id": "...", "rename_actions": true}

GET    /api/stats                            # Aggregate statistics
GET    /api/stats/meetings-over-time         # Weekly meeting count series
GET    /api/stats/by-type                    # Meeting type distribution
GET    /api/stats/action-velocity            # Created vs. completed actions per week

POST   /api/recording/start                  # Start recording, returns meeting_id
POST   /api/recording/stop                   # Stop recording, triggers pipeline
GET    /api/recording/status                 # Current recording state + elapsed time

GET    /api/config                           # Get current config
PATCH  /api/config                           # Update config
       { "recording": { "audio_device": "MeetingCapture" } }
GET    /api/config/provider-models?provider=X  # Fetch models (Anthropic/OpenAI/OpenRouter/Ollama)
GET    /api/config/custom-models             # Previously used custom models
GET    /api/config/hardware                  # Detect GPU/RAM, recommend models
GET    /api/config/transcription-engines     # List available transcription engines + install status

GET    /api/audio-devices                    # List available audio devices
GET    /api/auto-detect-device               # Auto-select best capture device

POST   /api/meetings/:id/regenerate          # Re-run minutes generation
POST   /api/meetings/:id/export              # Export (body: { format: "pdf" | "md" })
```

### 5.2 WebSocket

```
WS /ws/recording                             # Live recording status updates
   → { "state": "recording", "elapsed_seconds": 42, "audio_level": 0.7 }
   → { "state": "processing", "step": "transcribing", "progress": 0.42 }
   → { "state": "done", "meeting_id": "..." }

WS /ws/pipeline/:meeting_id                  # Pipeline progress for a specific meeting
   → { "step": "transcribing", "progress": 0.8 }
   → { "step": "generating", "progress": 0.0 }
   → { "step": "done" }
```

### 5.3 Response Shapes

All list endpoints return:
```json
{
  "items": [...],
  "total": 47,
  "limit": 20,
  "offset": 0
}
```

Error responses:
```json
{
  "error": "Meeting not found",
  "detail": "No meeting with ID abc123"
}
```

---

## 6. Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Frontend framework** | Svelte 5 + SvelteKit | Minimal boilerplate, fast, small bundle |
| **Build** | Vite | Fast HMR, standard tooling |
| **Styling** | Tailwind CSS | Utility-first, easy dark mode, consistent spacing |
| **Charts** | Chart.js (via svelte-chartjs) | Lightweight, well-documented |
| **Markdown** | `marked` + `DOMPurify` | Render minutes safely |
| **HTTP client** | `fetch` (native) | No extra dependency needed |
| **Audio** | HTML5 `<audio>` + Web Audio API | Waveform visualization, time sync |
| **Backend** | FastAPI | Already in the stack, async, auto-docs |
| **WebSocket** | FastAPI WebSocket | Live recording status |
| **Serving** | FastAPI serves Svelte build as static files | Single process, no nginx |

### 6.1 Frontend Directory Structure

```
web/
├── package.json
├── svelte.config.js
├── vite.config.js
├── tailwind.config.js
├── src/
│   ├── app.html                    # HTML shell
│   ├── app.css                     # Tailwind imports + custom properties
│   ├── lib/
│   │   ├── api.js                  # API client (fetch wrappers)
│   │   ├── stores/
│   │   │   ├── meetings.js         # Meeting list store
│   │   │   ├── recording.js        # Recording state store (WebSocket)
│   │   │   └── theme.js            # Dark mode store
│   │   └── components/
│   │       ├── MeetingCard.svelte
│   │       ├── MeetingTypeBadge.svelte
│   │       ├── ActionItemRow.svelte
│   │       ├── AudioPlayer.svelte
│   │       ├── SearchBar.svelte
│   │       ├── MarkdownRenderer.svelte
│   │       ├── PersonAvatar.svelte
│   │       ├── TagEditor.svelte
│   │       ├── StatusStepper.svelte
│   │       ├── EmptyState.svelte
│   │       ├── ConfirmModal.svelte
│   │       ├── Toast.svelte
│   │       └── Skeleton.svelte
│   └── routes/
│       ├── +layout.svelte          # Shell (sidebar + top bar)
│       ├── +page.svelte            # / (Meetings list)
│       ├── meeting/
│       │   └── [id]/+page.svelte   # /meeting/:id (Meeting detail)
│       ├── actions/+page.svelte    # /actions
│       ├── decisions/+page.svelte  # /decisions
│       ├── people/
│       │   ├── +page.svelte        # /people
│       │   └── [id]/+page.svelte   # /people/:id
│       ├── stats/+page.svelte      # /stats
│       ├── record/+page.svelte     # /record
│       └── settings/+page.svelte   # /settings
└── static/
    └── favicon.svg
```

---

## 7. Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+K` / `Ctrl+K` | Focus global search (command palette) |
| `Cmd+R` / `Ctrl+R` | Toggle recording start/stop |
| `Esc` | Close modal / clear search / navigate back |
| `J` / `K` | Navigate down/up in meeting list |
| `Enter` | Open selected meeting |
| `Space` | Toggle audio play/pause (on transcript tab) |
| `←` / `→` | Seek audio ±5 seconds |

---

## 8. Accessibility

- All interactive elements are keyboard-navigable
- ARIA labels on icon-only buttons
- Focus rings visible in keyboard-navigation mode (hidden on mouse click)
- Color is never the only differentiator (icons + text accompany color coding)
- Respects `prefers-reduced-motion` (disables transitions)
- Minimum contrast ratios meet WCAG AA (4.5:1 for text)

---

## 9. Launch Configuration

```yaml
# config/config.yaml additions
api:
  enabled: true
  host: "127.0.0.1"
  port: 8080

web_ui:
  enabled: true
  port: 3000              # Dev server port (Vite); production is served by FastAPI
```

**Development**:
```bash
# Terminal 1: API server
mm serve                  # FastAPI on :8080

# Terminal 2: Svelte dev server with HMR
cd web && npm run dev     # Vite on :3000, proxies /api → :8080
```

**Production**:
```bash
cd web && npm run build   # Build to web/build/
mm serve                  # FastAPI serves API + static build on :8080
```

Single process, single port, no reverse proxy needed.
