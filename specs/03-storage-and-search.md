# System 3: Meeting Minutes Storage & Search

## Overview

A storage and retrieval system for meeting minutes, transcripts, and metadata. Starts as a simple SQLite-backed store with full-text search and a CLI interface. Designed with clean abstractions so semantic search, a web UI, analytics, and integrations can be layered on later.

---

## 1. Data Model

### 1.1 Core Entities

```
Meeting
├── meeting_id (UUID, primary key)
├── title
├── date
├── duration
├── platform
├── meeting_type
├── organizer
├── status (draft | reviewed | approved)
├── created_at
├── updated_at
│
├── Transcript (1:1)
│   ├── full_text
│   ├── segments[] (with timestamps, speakers)
│   ├── language
│   └── audio_file_path
│
├── Minutes (1:1)
│   ├── minutes_id
│   ├── markdown_content
│   ├── summary
│   ├── sentiment
│   ├── structured_json (JSON blob of full structured output)
│   ├── generated_at
│   ├── llm_model
│   └── review_status
│
├── ActionItems[] (1:many)
│   ├── action_item_id
│   ├── description
│   ├── owner
│   ├── due_date
│   ├── priority (high | medium | low)
│   ├── status (open | in_progress | done | cancelled)
│   ├── linked_meeting_ids[] (tracks across meetings)
│   └── mentioned_at_seconds
│
├── Decisions[] (1:many)
│   ├── decision_id
│   ├── description
│   ├── made_by
│   ├── rationale
│   ├── confidence (high | medium | low)
│   └── mentioned_at_seconds
│
├── Attendees[] (many:many)
│   ├── person_id
│   ├── name
│   ├── email
│   └── role (organizer | attendee | optional)
│
├── Topics[] (many:many)
│   ├── topic_id
│   ├── name
│   └── auto_extracted (bool)
│
└── Tags[] (many:many)
    ├── tag_id
    └── name
```

### 1.2 Person Entity (Cross-meeting)

```
Person
├── person_id (UUID)
├── name
├── email
├── aliases[] (alternative names/spellings)
├── department
├── role/title
├── meetings_count
├── last_meeting_date
└── voice_profile_id (optional, links to System 1)
```

### 1.3 Topic Entity (Cross-meeting)

```
Topic
├── topic_id (UUID)
├── name (canonical name)
├── aliases[] (related terms)
├── meeting_count
├── first_mentioned_date
└── last_mentioned_date
```

---

## 2. Storage Backend

### 2.1 Primary Database — SQLite (MVP)

- SQLite for single-user local setup (zero configuration, portable)
- Schema migrations via `alembic`
- All core entities stored in a single `meetings.db` file
- Use SQLAlchemy as the ORM to keep the door open for swapping backends later

#### 2.1.1 Alembic Migrations

| Migration | Description |
|-----------|-------------|
| `001_initial_schema` | Creates all core tables (meetings, transcripts, minutes, action_items, decisions, persons, meeting_attendees) and FTS5 virtual table |
| `002_structured_minutes` | Adds `sentiment` and `structured_json` columns to minutes table, adds `priority` column to action_items, adds `rationale` and `confidence` columns to decisions |

### 2.2 Full-Text Search — SQLite FTS5 (MVP)

- Built-in full-text search extension, no extra infrastructure
- Supports prefix queries, phrase matching, boolean operators
- BM25 ranking
- Index transcript full text and minutes markdown content

> **Future**: Meilisearch can be added for faceted search, typo tolerance, and highlighting if FTS5 becomes limiting.

### 2.3 Semantic / Vector Search (Future)

Not included in the initial implementation. When added:

- Generate embeddings for meeting summaries, transcript segments, action items, decisions
- **Embedding models**: `sentence-transformers` (local) or OpenAI/Cohere (cloud)
- **Vector store**: `sqlite-vss`, `chromadb`, or `lancedb`

### 2.4 File Storage (MVP)

- Audio files: local filesystem
- Transcript JSON: stored in database + filesystem backup
- Minutes markdown: stored in database + filesystem backup

---

## 3. Search Capabilities

### 3.1 Full-Text Search (MVP)

Query the complete text of transcripts and meeting minutes using SQLite FTS5.

**MVP features:**
- Keyword search across all meetings
- Phrase matching (`"database migration"`)
- Boolean operators (`kubernetes AND deployment NOT staging`)
- Date range filtering (`after:2026-01-01 before:2026-03-31`)
- Meeting type filtering (`type:standup`)

**Example queries:**
```
"database migration" type:decision_meeting after:2026-01-01
speaker:Bob action_item:open
```

> **Future**: Field-specific search (`speaker:Alice topic:budget`), fuzzy matching, and compound queries combining full-text with semantic search.

### 3.2 Structured Queries (MVP)

Query specific metadata fields with filters.

**MVP filters:**
| Filter | Example | Description |
|--------|---------|-------------|
| Date range | `2026-Q1` | Meetings in a time period |
| Meeting type | `standup`, `decision_meeting` | By classification |
| Attendee | `alice@company.com` | Meetings a person attended |
| Action item status | `open` | Meetings with open action items |
| Tag | `engineering`, `client` | By user-assigned tag |

> **Future filters**: Organizer, platform, action item owner, topic, duration, speaker count, review status.

### 3.3 Semantic Search (Future)

Find meetings by meaning, not just keywords. When added:

- Natural language queries: "meetings where we discussed scaling the backend"
- Similar meeting discovery: "find meetings similar to this one"
- Concept search: finds relevant meetings even without exact keyword matches
- Cross-language search

### 3.4 Compound Queries (Future)

Combine full-text, structured, and semantic search:

```
semantic:"discussions about scaling" AND type:team_meeting AND attendee:alice@company.com AND after:2026-01-01
```

---

## 4. Browsing & Navigation

### 4.1 Views (MVP)

| View | Description |
|------|-------------|
| **Timeline** | Chronological list of all meetings |
| **By Type** | Group by meeting type (all standups, all customer meetings, etc.) |
| **Action Items** | Cross-meeting view of all action items, filterable by owner/status/date |

> **Future views**: Calendar view, By Person, By Topic, Decisions log, Recurring meeting series tracking.

### 4.2 Meeting Detail View (MVP)

For each meeting, display:

- Meeting minutes (rendered markdown)
- Metadata (date, attendees, type, tags)
- Action items with status toggles
- Decisions list
- Link to original transcript (expandable)

> **Future**: Audio playback synced to transcript timestamps, related meetings via semantic similarity, prev/next in recurring series.

### 4.3 Cross-Meeting Features (Future)

- **Action Item Tracking**: Track action items across meetings. If "Bob will review PR #423" is open from Monday's standup, flag it in Wednesday's standup if still unresolved.
- **Topic Evolution**: See how a topic has evolved across meetings over time (e.g., "database migration" discussed in 5 meetings over 3 weeks)
- **Person Activity**: See all meetings a person participated in, their action items, decisions they were involved in
- **Decision Log**: Filterable log of all decisions across all meetings, with links to the meeting where they were made

---

## 5. Indexing & Sync Pipeline

### 5.1 Ingestion Pipeline (MVP)

```
System 2 Output (JSON)
    │
    ├── Parse & validate JSON
    ├── Extract/update Person entities
    ├── Store Meeting, Transcript, Minutes
    ├── Store Action Items, Decisions
    └── Update full-text search index (FTS5)
```

> **Future**: Extract/update Topic entities, generate embeddings for semantic search, trigger post-indexing hooks.

### 5.2 Incremental Updates (MVP)

- Watch the output directory of System 2 for new/updated files
- Re-index only changed content
- Support manual re-indexing trigger

### 5.3 Embedding Generation (Future)

When semantic search is added:

- Generate embeddings at multiple granularities:
  - **Meeting-level**: Embed the full summary (for meeting-to-meeting similarity)
  - **Section-level**: Embed each section of the minutes (for topic search)
  - **Segment-level**: Embed transcript segments (for precise quote finding)
  - **Action item level**: Embed each action item (for task search)
- Batch embedding generation for efficiency
- Incremental embedding updates when minutes are edited

---

## 6. API & Interfaces

### 6.1 CLI Interface (MVP)

The CLI is the primary interface for the initial version.

```bash
# Initialize
mm init                                  # Create database + data directories

# Search
mm search "database migration"
mm search --type standup --after 2026-03-01

# Browse
mm list                                  # Recent meetings
mm list --person alice@company.com       # Meetings with Alice
mm show <meeting_id>                     # Display meeting minutes
mm transcript <meeting_id>               # Display transcript

# Action items
mm actions                               # All open action items
mm actions --owner bob@company.com       # Bob's open actions
mm actions --overdue                     # Overdue action items
mm actions complete <action_id>          # Mark action as done

# Management
mm tag <meeting_id> engineering client   # Add tags
mm retranscribe <meeting_id>             # Re-run transcription
mm regenerate <meeting_id>               # Re-generate minutes
mm export <meeting_id> --format pdf      # Export meeting

# Server
mm serve                                 # Start web UI + API server at :8080
mm serve --host 0.0.0.0 --port 9090     # Custom host/port
```

> **Future CLI additions**: `mm search --semantic "..."` for semantic search.

### 6.2 REST API (Implemented)

The REST API is implemented with FastAPI and serves all data via 32 endpoints:

```
GET    /api/meetings                     # List meetings (paginated, filtered)
GET    /api/meetings/:id                 # Get meeting detail
GET    /api/meetings/:id/transcript      # Get transcript
GET    /api/meetings/:id/minutes         # Get minutes (markdown or JSON)
GET    /api/meetings/:id/audio           # Stream audio file
PATCH  /api/meetings/:id                 # Update metadata, tags, status
DELETE /api/meetings/:id                 # Delete meeting and associated data

GET    /api/search?q=...                 # Full-text search
GET    /api/search/semantic?q=...        # Semantic search
GET    /api/search/similar/:id           # Find similar meetings

GET    /api/action-items                 # List all action items (filtered)
PATCH  /api/action-items/:id             # Update action item status

GET    /api/decisions                    # List all decisions (filtered)

GET    /api/people                       # List all known people
GET    /api/people/:id/meetings          # Meetings for a person

GET    /api/topics                       # List all topics
GET    /api/topics/:id/meetings          # Meetings for a topic

GET    /api/stats                        # Usage statistics
```

### 6.3 Web UI (Implemented)

A browser-based interface built with Svelte + SvelteKit + Tailwind CSS:

- Served at `localhost:8080` (production: FastAPI serves both API and static build)
- Development mode: Svelte dev server at `:3000` proxies `/api` to `:8080`
- **Features**:
  - Search bar with `Cmd+K` shortcut and filters
  - List/grid/calendar views
  - Meeting detail page with rendered minutes, audio player, action items, decisions
  - Action item dashboard with cross-meeting tracking
  - Decision log
  - Stats page with charts
  - Recording page with live timer and audio levels
  - Settings editor
  - Dark mode support

### 6.4 Integrations (Future)

| Integration | Feature |
|-------------|---------|
| **Slack bot** | `/meeting search <query>` — search meetings from Slack |
| **Obsidian plugin** | Sync minutes as Obsidian notes with backlinks |
| **Notion sync** | Auto-publish minutes to Notion database |
| **Confluence sync** | Auto-publish minutes to Confluence space |
| **Google Docs** | Create Google Doc per meeting with Drive folder structure |
| **Calendar** | Attach minutes link to calendar events |

---

## 7. Analytics & Insights (Future)

Not included in the initial implementation. When added:

### 7.1 Meeting Statistics

- Total meetings per week/month
- Average meeting duration by type
- Time spent in meetings per person
- Most frequent meeting attendees
- Meeting type distribution

### 7.2 Action Item Analytics

- Open vs. completed action items over time
- Average time to completion
- Action items per person
- Overdue action item alerts
- Recurring blockers (same action item appearing across meetings)

### 7.3 Topic Trends

- Most discussed topics over time
- Trending topics (increasing mention frequency)
- Topic co-occurrence (topics frequently discussed together)
- Topic sentiment over time

### 7.4 Reports

- Weekly/monthly meeting summary digest
- Per-person meeting load report
- Action item status report
- Decision log export

---

## 8. Data Management

### 8.1 Backup & Export

- Automated database backups (configurable schedule)
- Full export to JSON/CSV for portability
- Individual meeting export (minutes + transcript + audio)
- Bulk export with filters

### 8.2 Retention Policies

```yaml
retention:
  audio_files:
    default_days: 90
    by_type:
      client_call: 365
      interview: 365
      other: 90
  transcripts:
    default_days: -1               # keep indefinitely
  minutes:
    default_days: -1               # keep indefinitely
  embeddings:
    regenerate_on_model_update: true
```

### 8.3 Single-User Mode

- No authentication required (local deployment)
- All data is accessible to the local user
- No access control or role management needed

---

## 9. Tech Stack

### MVP

| Component | Technology |
|-----------|-----------|
| Database | SQLite |
| ORM | SQLAlchemy |
| Migrations | Alembic |
| Full-text search | SQLite FTS5 |
| CLI | `typer` |
| File watching | `watchdog` |
| Backup | `sqlite3 .backup` |

| REST API | FastAPI + uvicorn |
| Web UI | Svelte + SvelteKit + Tailwind CSS |

### Future Additions

| Component | Technology |
|-----------|-----------|
| Vector store | `sqlite-vss` / `chromadb` |
| Embeddings | `sentence-transformers` / OpenAI Embeddings |
| Full-text search (advanced) | Meilisearch |
| Background jobs | `celery` or `apscheduler` |
