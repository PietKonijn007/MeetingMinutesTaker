# BRF-2 — Topic + Focus-area Prep Brief

**Status:** Implemented.
**Scope tag:** Single-user, local-first MVP. No multi-user, no cloud, no auto-email.
**Builds on:** BRF-1 (`/brief` page, `GET /api/brief`, `BriefingPayload`), REC-1 (series threading), SPK-1 (speaker centroids), ANA-1 (cross-meeting analytics), the RAG chat stack (sqlite-vec + sentence-transformers).

## 1. Problem

BRF-1 already produces a strong pre-meeting briefing for a known **attendee set**. It returns six pure-query sections (who/when last, open commitments, unresolved topics, sentiment sparklines, recent decisions, context excerpts) and pre-fills a Start Recording panel. What it does not yet do:

- **Take a topic as a first-class signal.** Today the brief is keyed on attendees + optional meeting type. The "context excerpts" section retrieves transcript chunks by attendee overlap, not by what the meeting is *about*. A 1:1 with the same direct report can be about pricing, hiring, or a roadmap dispute — and the brief looks the same.
- **Take focus areas.** The user usually walks into a meeting with a few specific things they want to find out — "where did we land on the SLA?", "what's the current state of the migration?", "what did Jon say about the timeline last time?". Today there's no input slot for those.
- **Produce an exportable artifact.** The brief renders in the web UI only. There is no markdown / PDF / file the user can read on their phone, paste into Obsidian, or hand off.
- **Synthesize talking points.** The optional `summarize_with_llm` toggle attaches a two-sentence summary, but there are no concrete suggested talking points grounded in the retrieved data.

BRF-2 keeps the BRF-1 pipeline and payload as the foundation and **layers on**: topic-driven RAG retrieval, user-supplied focus areas, an exportable brief artifact, and grounded LLM talking points. It does not replace `/brief`, the `GET /api/brief` endpoint, or `BriefingPayload`. Brief generation is always **on demand** — there is no scheduled or background auto-generation in this spec, and there is no calendar integration. The user supplies the inputs.

## 2. Inputs (extends BRF-1)

BRF-1 inputs (kept as-is):
- `person_ids[]` — list of attendees.
- `meeting_type` — optional, biases the layout.

BRF-2 adds three user-supplied fields. No calendar resolution, no event ID, no automatic attendee discovery.

- **`topic`** *(string, required for BRF-2 features)* — free-text description of what the meeting is about. Used as the query string for topic-RAG retrieval and as a primary signal for talking-point generation. Example: *"Q3 vendor pricing review"*, *"hiring loop debrief for the platform role"*, *"production incident postmortem from Tuesday"*.
- **`focus_items[]`** *(list of strings, optional, max 10)* — specific things the user wants the brief to dig up. Each item is a short natural-language phrase or question. Examples: *"What did we decide about the SLA penalties?"*, *"Outstanding asks from Jon"*, *"Anything new on the migration timeline since April"*. Each focus item drives its own RAG sub-query and produces its own findings block in the output.
- **`since`** *(date, optional, default = `now − brief.lookback_days`)* — caps how far back retrieval searches.

The existing query params (`person_ids`, `meeting_type`) keep working unchanged. BRF-1 callers see no breaking change.

### 2.1 How the user provides them

**Web UI (`/brief`)** — three inputs added to the existing form:
1. Attendees picker (already exists).
2. **Topic** — single-line text input, placeholder *"What is this meeting about?"*.
3. **Focus areas** — multi-line text area where each non-empty line becomes one `focus_items[]` entry, plus a small "+" button to add structured entries. Empty until the user types something.

**CLI** —
```
mm brief --attendees "Jon Porter,Sarah Lee" \
         --topic "Q3 vendor pricing review" \
         --focus "Outstanding asks from Jon" \
         --focus "What did we decide about SLA penalties?" \
         --focus "Migration timeline updates since April" \
         [--type vendor_call] [--since 2026-01-01] \
         [--format md|json] [--out path]
```
`--focus` is repeatable. `--attendees` accepts a comma-separated list of names or emails; both resolve to `PersonORM` rows. Unknown names print a warning and are dropped (the user is told which names didn't match so they can fix typos or create the person first).

## 3. Behavior

### 3.1 Pipeline (additive)

```
                          BRF-1 sections (unchanged)
                          ───────────────────────────
   inputs ──► who_and_when_last
          ──► open_commitments
          ──► unresolved_topics
          ──► recent_sentiment
          ──► recent_decisions
          ──► context_excerpts ────┐
          ──► suggested_start      │
                                   │  topic (if present) reroutes
                                   ▼  this section's query
                          ┌────────────────────────┐
                          │  Topic-RAG retrieval   │  ◄── NEW
                          └────────────────────────┘

                          BRF-2 additions
                          ───────────────
        focus_items[] ──► per-focus RAG retrieval ──► focus_findings[] ◄── NEW
                                                │
                          ──► talking_points (LLM, grounded by topic + focus + payload)  ◄── NEW
                          ──► export artifacts (md / json)                                ◄── NEW
```

The six BRF-1 sections still run as pure SQL. The only retrieval change inside BRF-1's pipeline is in `context_excerpts`: when a `topic` is supplied, the embedding query becomes `topic` (or `topic || " " || meeting_title`) instead of the current attendee-derived query. When `topic` is absent, behavior is unchanged.

### 3.2 New section: `focus_findings`

For each `focus_items[]` entry the brief runs an independent retrieval pass and emits one findings block. This is the primary user value-add: the user told us exactly what they care about, so we answer those things directly instead of hoping it falls out of a generic summary.

For each focus item:
1. **Embedding search** over transcripts + minutes scoped to the attendee set's history (and `since` window) — top K chunks (default K=5, configured by `brief.focus.top_k`).
2. **Structured-data scan** — SQL queries against action items, decisions, and open questions, filtered by attendee overlap and matched against the focus phrase via simple keyword + embedding similarity (reuses the same embedder as the chat feature).
3. **LLM synthesis** — given the focus item, the retrieved chunks, and any matching structured rows, the LLM produces a 2–4 sentence answer with inline citations. If retrieval returned nothing relevant (top score below `brief.focus.min_score`, default 0.55), the answer is the literal string `"No relevant history found."` and **no LLM call is made** for this item.

Schema:

```python
class BriefFocusFinding(BaseModel):
    focus: str                          # echo of the input phrase
    answer: str                         # 2–4 sentence synthesis or "No relevant history found."
    citations: list[BriefCitation]      # may be empty when no history
    related_actions: list[str] = []     # action_ids surfaced by the SQL scan
    related_decisions: list[str] = []   # decision_ids surfaced by the SQL scan
```

The LLM provider follows `brief.llm_provider` (defaults to `inherit`). All four supported providers (Claude / OpenAI / OpenRouter / Ollama) work, so fully-offline operation is preserved.

### 3.3 New section: `talking_points`

A list of LLM-generated suggested points the user should raise, grounded in the rest of the payload. Generation runs only when `brief.talking_points.enabled` is true (defaults to `true` when `summarize_with_llm` is true; otherwise `false`).

Schema:

```python
class BriefTalkingPoint(BaseModel):
    text: str                          # 1–2 sentences, action-oriented phrasing
    rationale: str                     # why this matters now
    citations: list[BriefCitation]     # at least one — enforced post-generation
    priority: Literal["high", "medium", "low"]

class BriefCitation(BaseModel):
    kind: Literal["action", "decision", "open_question", "excerpt", "sentiment", "focus"]
    ref_id: str                        # action_id / decision_id / meeting_id+chunk_id / focus index
    meeting_id: str | None = None
    snippet: str | None = None
```

Prompt input is the already-built `BriefingPayload` (including `focus_findings`) rendered as compact JSON, the topic, and the focus items list. The prompt requires every emitted talking point to cite at least one of: an action item, a decision, an open question, a context excerpt, a sentiment trend, or a `focus_findings` entry. A post-generation validator drops any talking point with zero valid citations (defends against generic "discuss progress" filler) and logs a warning. If fewer than 2 points survive validation, the section is omitted rather than padded.

### 3.4 Updated payload

`BriefingPayload` gets four additive fields. Existing consumers ignore them.

```python
class BriefingPayload(BaseModel):
    # ... all existing fields ...
    topic: str | None = None                          # NEW (echoed back)
    focus_items: list[str] = []                       # NEW (echoed back)
    focus_findings: list[BriefFocusFinding] = []      # NEW
    talking_points: list[BriefTalkingPoint] = []      # NEW
```

### 3.5 Export artifacts

A new endpoint `GET /api/brief/export?format={md|json}&...` returns the brief as a file. Same query params as `GET /api/brief`. `format=md` produces a stable-ordered markdown document; `format=json` returns the full `BriefingPayload`. The markdown layout:

```
# Prep Brief — <topic>
<date · attendees>

## TL;DR
<existing summary, when present>

## Suggested talking points
1. <text> — <rationale>  [→ ACT-123, M-456]
...

## What you asked about
### <focus item 1>
<2–4 sentence answer> [→ M-456 · 2026-04-22]
Related: ACT-123 (open), DEC-78

### <focus item 2>
No relevant history found.

## Open commitments
### To <attendee>
- ...
### From <attendee>
- ...

## Unresolved questions
- ...

## Recent decisions
- ...

## Recent context (last 3 relevant meetings)
### <date> — <title>
<excerpt>

## Sentiment trend
<sparkline as unicode blocks per attendee>
```

Empty sections are omitted (matches the minutes-rendering convention). The "What you asked about" section is omitted entirely when `focus_items` is empty. The web UI gets a "Download brief" button that calls `?format=md`.

### 3.6 Caching (on-demand only)

Briefs are generated **only** when the user asks for one — via the web UI or the `mm brief` CLI. There is no scheduled job, no calendar polling, and no background pre-generation.

When a brief is built it is persisted (markdown + JSON + a `meeting_briefs` row) so a subsequent identical request is cheap. The cache key is `(attendee_set_hash, topic_hash, focus_items_hash, meeting_type)`. A cached brief is reused when the user re-requests the same brief and **none** of the following has happened:

- A new meeting completed whose attendee set overlaps the brief's attendee set.
- An action item involving any attendee was created, edited, or closed.
- The user clicked "Refresh brief."
- The cached row is older than `cache.max_age_minutes`.

Invalidation is checked at request time (cheap SQL `MAX(updated_at)` lookups against existing tables); no event bus is needed.

## 4. Configuration

Extends the existing `BriefConfig` (see `src/meeting_minutes/config.py`). All new keys default to safe values that preserve current BRF-1 behavior.

```jsonc
{
  "brief": {
    // existing keys
    "summarize_with_llm": false,

    // NEW — BRF-2
    "lookback_days": 90,
    "topic_rag_top_k": 8,
    "llm_provider": "inherit",          // inherit | claude | openai | openrouter | ollama
    "focus": {
      "max_items": 10,
      "top_k": 5,
      "min_score": 0.55                 // below this, emit "No relevant history found."
    },
    "talking_points": {
      "enabled": null,                  // null = follow summarize_with_llm
      "max": 5,
      "require_citation": true
    },
    "export": {
      "output_dir": "data/briefs"
    },
    "cache": {
      "max_age_minutes": 240
    }
  }
}
```

## 5. Storage

One new table; no changes to existing BRF-1 storage (BRF-1 doesn't persist briefs).

`meeting_briefs`:

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `attendee_set_hash` | TEXT | sha256 of sorted person_ids; reuses `compute_attendee_hash` from REC-1 |
| `topic` | TEXT NULL | |
| `topic_hash` | TEXT NULL | |
| `focus_items` | JSON | List of focus phrases as supplied |
| `focus_items_hash` | TEXT | sha256 of normalized focus list (lowercased, sorted) |
| `meeting_type` | TEXT NULL | |
| `markdown_path` | TEXT | |
| `json_path` | TEXT | |
| `generated_at` | TIMESTAMP | |
| `model` | TEXT NULL | Provider/model used (NULL when LLM disabled) |
| `source_meeting_ids` | JSON | Cited meetings, used for invalidation |
| `superseded_by` | INTEGER NULL | FK to a newer row |

Index on `(attendee_set_hash, topic_hash, focus_items_hash, generated_at DESC)`.

Briefs are not added to FTS or vector search — they're derivative artifacts and indexing them would pollute meeting-history retrieval.

## 6. Out of scope (v1)

- **Calendar integration of any kind.** No ICS, no EventKit, no Google/Microsoft APIs, no auto-attendee discovery, no auto-topic extraction. The user types attendees and topic in.
- **Scheduled / background auto-generation.** Briefs are produced only when the user requests one.
- **Attached document fetching from invites.** Use spec 09 (attachments) if the user wants a doc summarized into the brief.
- Multi-user — briefs are written from "your" perspective.
- Push delivery (Slack / email).
- Diff briefs ("what changed since the last brief for this series") — deferred.

## 7. Success criteria

- For an attendee set with 60 days of history, a 4-word topic, and 3 focus items, BRF-2 with talking points enabled completes in ≤ 12 s on the reference Apple Silicon machine using the local stack (Ollama + sqlite-vec). Per-focus retrieval runs in parallel.
- Without `topic` and `focus_items`, BRF-2 returns a payload byte-identical to BRF-1 (modulo the new optional fields being absent / empty) — verified by a regression test that snapshots BRF-1's `BriefingPayload` for a fixed input and re-runs it through the BRF-2 endpoint.
- A focus item with no matching history (top similarity < `brief.focus.min_score`) emits exactly the literal `"No relevant history found."` and **no LLM call** is made — verified by a unit test that mocks the embedder to return low scores and asserts the LLM client was not invoked.
- Every emitted talking point cites at least one item already present in the rest of the payload (action, decision, open question, excerpt, sentiment point, or focus finding). Enforced by a unit test that mocks the LLM with a citation-less response and asserts the validator drops it.
- `GET /api/brief/export?format=md` produces a stable, deterministic markdown for the same inputs (no timestamps in the body, only in the header) — verified by a golden-file test.
- `mm brief --attendees ... --topic ... --focus ... --focus ...` writes both `.md` and `.brief.json` to `data/briefs/` and exits 0; unknown attendee names produce a clear stderr warning and don't fail the command.
- Re-requesting an unchanged brief within `cache.max_age_minutes` returns the cached row in < 200 ms (no LLM call, no embedding query).
