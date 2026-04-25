# System 2: Meeting Minutes Generation

## Overview

An LLM-powered system that takes structured transcript JSON from System 1 and generates tailored meeting minutes. It uses meeting type classification to select the appropriate prompt template, producing well-structured, actionable meeting minutes.

---

## 1. Input Processing

### 1.1 Transcript Ingestion

- Accept transcript JSON from System 1 (file path, stdin, or API call)
- Validate JSON against the transcript schema
- Extract key fields needed for prompt construction:
  - `meeting_type` and confidence score
  - `calendar` metadata (title, attendees, organizer)
  - `speakers` mapping
  - `transcript.full_text` and `transcript.segments`

### 1.2 Pre-processing

- Replace speaker labels with actual names (e.g., `SPEAKER_00` -> `Alice`)
- Merge short consecutive segments from the same speaker
- Remove filler words and false starts (optional, configurable)
- Truncate or chunk very long transcripts to fit LLM context windows
- For transcripts exceeding context limits: use a sliding window with overlap, summarize each chunk, then combine

---

## 2. Meeting Type Router

### 2.1 Classification Validation

- Use the `meeting_type` from System 1 as the primary classification
- If `meeting_type_confidence < 0.7`, run the LLM-based classifier automatically (see Section 2.4)
- Allow user override via CLI flag or config

### 2.4 LLM-Based Meeting Type Classifier

The primary classifier is an LLM call using Claude Haiku with Anthropic `tool_use`. A heuristic classifier (calendar title + content keywords + attendee count) is used as a fallback when the LLM is unreachable.

**How the LLM path works:**
1. Sends the first 4000 characters of the transcript plus metadata (speaker count, calendar title, attendee count) to Claude Haiku
2. Uses Anthropic `tool_use` with an enum constraint (populated from the on-disk template list) to guarantee a valid meeting type is returned
3. The LLM returns `meeting_type`, `confidence` (0-1), and `reasoning`
4. The classifier reads actual template descriptions (system prompt + section headings) via `_extract_type_descriptions()` to inform its decision
5. Custom template types added to the `templates/` directory are auto-discovered (files starting with `_` are treated as shared macro includes and excluded from the type list)

**Cost:** Approximately $0.001 per classification.

**Heuristic fallback** (when no `ANTHROPIC_API_KEY` is set or the API call fails):
1. Calendar title keywords first — e.g. "QBR" / "vendor sync" → `vendor_meeting`; "post-mortem" / "RCA" → `incident_review`; "board meeting" → `board_meeting`; "interview debrief" → `interview_debrief`; "architecture review" / "design review" → `architecture_review`; "staff meeting" / "E-team" → `leadership_meeting`
2. Transcript content keywords (lower confidence)
3. Attendee count heuristic — two attendees → `one_on_one`
4. Otherwise `other` (falls through to the general template)

The fallback returns low confidence (0.3-0.55) so the LLM path is preferred whenever reachable.

**Trigger:** Runs automatically when the initial transcript confidence is below 0.7.

**Meeting Type Refinement (N11):** After the initial classification, the system can refine the meeting type based on the full transcript content, improving accuracy for borderline cases. The refined type must match a known `MeetingType` enum value; free-form suggestions are logged but not applied.

### 2.2 Supported Meeting Types & Templates

Every template emits a shared baseline — **TL;DR** (~100 words), decisions, action items, risks, open questions, follow-up email draft, confidentiality classification — plus the type-specific sections below. **Empty sections are omitted entirely**; templates never emit "Not discussed" placeholders.

**Team & cadence**

| Meeting Type | Template Focus | Key Sections |
|-------------|---------------|--------------|
| `standup` | Per-person updates | Per-person Done/Today/Blockers |
| `team_meeting` | Decisions, financials, strategy | Decisions (with rationale), Financial review, Blockers (4 categories + cross-team), Strategic updates, Technology decisions, Vendor feedback, Customer impact, Resource & capacity, Team health, Announcements, Parking lot, Action items (by urgency) |
| `retrospective` | Improvement actions | What went well, What could be better, Stop/Start/Continue, Decisions, Action items |
| `planning` | Sprint / project / quarterly plan | Planning context, Scope & goals, Priorities, Team assignments, Timeline, Dependencies & risks |
| `brainstorm` | Ideas and themes | Problem statement, Ideas generated, Top ideas, Ideas to explore further |
| `decision_meeting` | Options and outcomes | Context, Options considered, Decision (with rationale, reversibility, scope, dissent) |

**1:1 (perspective-aware)**

| Meeting Type | Template Focus | Key Sections |
|-------------|---------------|--------------|
| `one_on_one_direct_report` | Manager→report: coaching, growth, performance | Mood/Energy, Wins, Progress vs. objectives, Blockers (4 categories), Feedback Given (SBI), Feedback Received (upward), Career Development, Coaching Notes, Engagement Signals, split Action Items |
| `one_on_one_leader` | User→boss / skip-level | Direction received, Feedback both ways, Leader commitments, Strategic & political context, Career development |
| `one_on_one_peer` | Peer 1:1 (no reporting line) | Alignment reached, Disagreements, Cross-team dependencies, Commitments (both directions), Information shared |
| `one_on_one` | Generic 1:1 fallback | Used when perspective isn't identifiable |

**Exec & cross-functional**

| Meeting Type | Template Focus | Key Sections |
|-------------|---------------|--------------|
| `leadership_meeting` | Peer-exec staff meeting | Cross-functional decisions, Priority & resource trade-offs, Cross-team commitments, Strategic alignment, Organizational signals, Financial signals |
| `board_meeting` | Board / investor update | Resolutions passed (with vote counts), Management update, Financial update, Strategic items, Asks of the board, Board feedback |
| `architecture_review` | ADR-style design review | Problem statement, Requirements & constraints, Options considered, Evaluation matrix, Decision (with reversibility), Migration plan |
| `incident_review` | Blameless post-mortem | Incident summary (severity, impact, timestamps), Timeline, Impact, Contributing factors, What went well / poorly, Corrective actions ([Prevent] / [Detect] / [Mitigate]) |

**External**

| Meeting Type | Template Focus | Key Sections |
|-------------|---------------|--------------|
| `customer_meeting` | Client / external call | Customer requirements, Vendor feedback, Customer blockers (4 categories), Demo notes, split Commitments (ours vs customer's), Next steps, Competitive intelligence |
| `vendor_meeting` | Vendor / partner / procurement (e.g. QBR) | Vendor commitments (as action items with vendor as owner), Roadmap updates, SLA performance, Commercial / pricing, Our asks / escalations, Competitive context |
| `interview_debrief` | Candidate interview debrief | Panel recommendation + level consistency, Per-interviewer signal, Per-competency assessment, Strengths, Concerns / gaps, Missing data |

**Fallback**

| Meeting Type | Template Focus | Key Sections |
|-------------|---------------|--------------|
| `other` | General-purpose | TL;DR + summary + decisions + action items + open questions; used when classification confidence is low |

### 2.3 Template Selection Logic

```
1. If user override provided -> use override
2. Check meeting_type from transcript JSON
3. If confidence >= 0.7 -> use corresponding template
4. If confidence < 0.7 -> run LLM classifier on transcript excerpt
5. If LLM unavailable -> fall back to heuristic (calendar title > content keywords > attendee count)
6. If still no match -> fall back to "other" (general-purpose) template
```

---

## 3. Prompt Engineering

### 3.1 Prompt Architecture

Each meeting type has a structured prompt composed of:

```
[System Prompt]
  - Role definition (you are a meeting minutes assistant)
  - Output format instructions
  - Quality guidelines

[Meeting Type Prompt]
  - Type-specific extraction instructions
  - Section structure for this meeting type
  - Examples of good output for this type

[Context Block]
  - Meeting metadata (title, date, attendees, organizer)
  - Meeting type
  - Any user-provided context or agenda
  - Custom LLM instructions from the user (if provided during recording via live note-taking)

[Transcript Block]
  - The full (or chunked) transcript with speaker labels

[Output Instructions]
  - Specific formatting requirements
  - Language/tone instructions
  - Length constraints
```

**Note**: Users can provide custom LLM instructions during recording via the live note-taking feature. These instructions are appended to the context block and allow the user to direct the LLM to focus on specific topics, use particular formatting, or apply domain-specific rules.

### 3.2 Prompt Templates (Examples)

#### Standup Template

```markdown
## Extraction Instructions
For each speaker/participant, extract:
- What they completed since last standup
- What they plan to work on today
- Any blockers or dependencies they mentioned

## Output Format
### Daily Standup - {date}
**Attendees**: {attendee_list}

#### {Person 1}
- **Done**: ...
- **Today**: ...
- **Blockers**: ...

#### {Person 2}
...

### Team Blockers
- List any blockers that need team/management attention
```

#### Decision Meeting Template

```markdown
## Extraction Instructions
Identify:
- The decision(s) that needed to be made
- The options/alternatives discussed
- Arguments for and against each option
- The final decision(s) reached
- Who made or approved the decision
- Any dissenting opinions
- Action items resulting from the decision

## Output Format
### Decision Record - {title} - {date}
**Decision Maker(s)**: ...
**Attendees**: ...

#### Context
Brief background on why this decision was needed.

#### Options Considered
1. **Option A**: Description
   - Pros: ...
   - Cons: ...
2. **Option B**: Description
   - Pros: ...
   - Cons: ...

#### Decision
State the decision clearly.

#### Rationale
Why this option was chosen.

#### Action Items
- [ ] {action} - {owner} - {due date if mentioned}
```

### 3.3 Prompt Quality Guidelines

All prompts include these universal instructions:

- **Accuracy**: Only include information explicitly stated in the transcript. Do not infer or fabricate.
- **Attribution**: Attribute statements and action items to specific people.
- **Action Items**: Extract all action items with owner and due date (if mentioned).
- **Decisions**: Clearly mark any decisions made during the meeting.
- **Conciseness**: Summarize discussions; don't transcribe verbatim.
- **Tone**: Professional, neutral tone. Match the formality level to the meeting type.
- **Formatting**: Use markdown with headers, bullet points, and checklists.

---

## 4. LLM Integration

### 4.1 Supported LLM Backends

| Backend | Models | Use Case | Structured Output |
|---------|--------|----------|-------------------|
| Anthropic API | Claude Sonnet 4.6, Claude Opus 4.6 | Primary recommended backend | tool_use (native) |
| OpenRouter | 200+ models (Claude, Gemini, GPT, Llama, DeepSeek, Mistral, etc.) | Multi-provider access via unified API | JSON-mode |
| OpenAI API | GPT-4o, GPT-4.1 | Alternative backend | JSON-mode |
| **Ollama (local)** | Qwen2.5, Llama 3.1, Phi-4, Mistral, Gemma (any Ollama model) | **Privacy-first / offline / free** | JSON-mode |

#### Ollama Local LLM Details

Ollama provides a fully local, free alternative to cloud LLM providers. The integration:

- Uses Ollama's **OpenAI-compatible API** (`/v1/chat/completions`) via the `openai` Python SDK
- Supports **JSON-mode structured generation**: the schema is embedded in the system prompt and the model is instructed to return valid JSON
- Automatically **strips markdown code fences** from responses before JSON parsing
- **Model discovery**: `GET /api/config/provider-models?provider=ollama` queries the local Ollama instance for pulled models with size, family, and quantization info
- **Hardware recommendations**: `GET /api/config/hardware` detects GPU/RAM and recommends appropriate Ollama models
- **Configurable base URL**: `OLLAMA_BASE_URL` env var or `generation.llm.ollama.base_url` in config (default: `http://localhost:11434`)
- **Generous timeout**: 300 seconds default (local models can be slow, especially on CPU)
- **Cost tracking**: $0.00 per token (local models are free)

**Recommended Ollama models for meeting summarization:**

| Model | Params | VRAM/RAM Needed | Quality |
|-------|--------|-----------------|---------|
| `qwen2.5:7b` | 7B | ~5GB | Good for short meetings |
| `llama3.1:8b` | 8B | ~6GB | Good general purpose |
| `qwen2.5:14b` / `phi4:14b` | 14B | ~10GB | Good for most meetings |
| `qwen2.5:32b` | 32B | ~20GB | Near-cloud quality |
| `qwen2.5:72b` / `llama3.1:70b` | 70B+ | ~45GB | Cloud-equivalent |

### 4.2 Generation Configuration

```yaml
generation:
  templates_dir: templates              # Directory containing .md.j2 template files
  # Per-vendor service-feedback sub-sections in templates that emit a vendor-feedback
  # block. Empty list = single generic vendor-feedback block.
  vendors: [AWS, NetApp]
  # Length of the `detailed_notes` narrative.
  length_mode: concise                  # concise (~150-400w) | standard (~400-900w) | verbose (~900-1500w)
  # Emit a ready-to-send follow-up email draft (subject + to/cc + body) as a
  # structured field and markdown section.
  generate_email_draft: true
  # auto = LLM classifies; otherwise force a floor (public | internal | confidential | restricted)
  confidentiality_default: auto
  # Prior-action carryover (ACT-1): pull still-open action items from recent
  # meetings that share attendees and inject them into the prompt. The LLM can
  # mark any acknowledged-closed items; matching DB rows are updated during ingestion.
  close_acknowledged_actions: true
  prior_actions_lookback_meetings: 5

  llm:
    primary_provider: "anthropic"        # anthropic | openai | openrouter | ollama
    model: "claude-sonnet-4-6"           # Model ID (for openrouter, use prefixed IDs like "anthropic/claude-sonnet-4")
    fallback_provider: "openai"          # Fallback when primary fails (null to disable)
    fallback_model: "gpt-4o"
    temperature: 0.2                     # low temperature for factual extraction
    max_output_tokens: 4096
    retry_attempts: 3
    timeout_seconds: 120

    # Ollama-specific settings (only used when primary_provider = ollama)
    ollama:
      base_url: "http://localhost:11434"  # Ollama server URL (overridable via OLLAMA_BASE_URL env var)
      timeout_seconds: 300                # Local models can be slower than cloud APIs

    # For long transcripts
    chunking:
      strategy: "sliding_window"         # sliding_window | map_reduce | refine
      chunk_size_tokens: 80000
      overlap_tokens: 2000
```

### 4.3 Long Transcript Handling

For transcripts exceeding the LLM context window:

#### Strategy: Map-Reduce (Default for meetings > 2 hours)
1. Split transcript into chunks with overlap
2. Generate partial minutes for each chunk (map step)
3. Combine partial minutes into final cohesive document (reduce step)

#### Strategy: Refine (Default for meetings 1-2 hours)
1. Process first chunk, generate initial minutes
2. Process each subsequent chunk, refining/extending the minutes
3. Final pass to ensure consistency and completeness

#### Strategy: Sliding Window (Default for meetings < 1 hour)
1. Fit as much transcript as possible in a single context
2. If it doesn't fit, use the most recent portion with a summary of earlier content

---

## 4A. Structured JSON Output via Anthropic tool_use

### 4A.1 Overview

The primary method for generating structured meeting minutes is via Anthropic's `tool_use` feature. Instead of asking the LLM to produce free-text that must be parsed with regex, the system defines a tool schema (`StructuredMinutesResponse`) and forces the LLM to call it, guaranteeing valid JSON output.

### 4A.2 StructuredMinutesResponse Schema

The tool definition includes these fields:

- `title` (str): Specific, descriptive meeting title — used only when the user did NOT provide a title at recording time (or post-hoc via the detail page). A user-set title is read from `data/notes/{id}.json` and used verbatim; the LLM's `title` field is then ignored. The post-hoc rename path (`PATCH /api/meetings/:id` with `{title}`) also writes back to the notes sidecar so a future regeneration keeps the user's title.
- `tldr` (str): ~100-word executive digest — biggest decision, biggest risk, most urgent action, single takeaway
- `summary` (str): Executive summary (2-6 sentences depending on meeting type)
- `detailed_notes` (str): Long-form narrative of how the conversation unfolded; length governed by `generation.length_mode`
- `meeting_type_suggestion` (str, optional): Refined type suggestion (N11). Accepted only if it matches a known `MeetingType` enum value
- `confidentiality` (str): `public` | `internal` | `confidential` | `restricted`
- `sentiment` (str): Overall meeting sentiment (positive, neutral, negative, mixed)
- `participants` (list[ParticipantInfo]): Participant details with name, role, contribution summary, and per-speaker sentiment (positive/neutral/negative/mixed)
- `discussion_points` (list[DiscussionPoint]): Key topics with description, speaker, and outcome
- `action_items` (list[StructuredActionItem]): With description, owner, due_date, priority (high/medium/low), transcript_segment_ids
- `decisions` (list[StructuredDecision]): With description, made_by, rationale, confidence (high/medium/low), transcript_segment_ids
- `risks_and_concerns` (list[RiskConcern]): Identified risks with description, raised_by
- `open_questions` (list[OpenQuestion]): Questions raised but not resolved; each has `question`, `raised_by`, `owner`
- `follow_ups` (list[FollowUp]): Items needing follow-up with description, owner, timeline
- `parking_lot` (list[str]): Topics raised but deferred
- `prior_action_updates` (list[PriorActionUpdate]): For each prior open action item acknowledged in this meeting — `action_item_id` (from the prior-actions block injected into the prompt), `new_status` (`done` | `in_progress` | `cancelled`), `evidence` (short quote)
- `email_draft` (EmailDraft | null): Ready-to-send follow-up email with `subject`, `to`, `cc`, `body`. Null when no commitments warrant a follow-up
- `meeting_effectiveness` (MeetingEffectiveness): `had_clear_agenda`, `decisions_made`, `action_items_assigned`, `unresolved_items`
- `key_topics` (list[str]): Extracted topic labels

### 4A.3 Tool Use Approach

1. The `LLMClient.generate_structured()` method constructs a tool definition from the schema
2. The tool is passed with `tool_choice: {"type": "tool", "name": "structured_minutes"}` to force the LLM to use it
3. The response is parsed directly into a `StructuredMinutesResponse` Pydantic model
4. A `StructuredMinutesAdapter` converts the structured response into the standard `ParsedMinutes` format used by the rest of the pipeline

### 4A.3.1 Persistence of structured data

The `MinutesJSONWriter` in `system2/output.py` populates `MinutesJSON.structured_data` with a dict containing all structured fields (`tldr`, `confidentiality`, `sentiment`, `participants`, `discussion_points`, `risks_and_concerns`, `open_questions`, `follow_ups`, `parking_lot`, `prior_action_updates`, `email_draft`, `key_topics`, `meeting_effectiveness`, `decisions`, `action_items`). This field is serialised by `StorageEngine.upsert_meeting()` into the `minutes.structured_json` TEXT column, and deserialised by the API route to populate the `MinutesResponse` seen by the frontend. This ensures the structured view in the Minutes tab works end-to-end (before this fix, structured fields existed on disk but were NULL in the DB, so the UI only showed Summary + Decisions + Actions).

### 4A.3.2 Prior-action carryover & auto-closure

Before generation, `PipelineOrchestrator` calls `StorageEngine.get_open_action_items_for_attendees()` to fetch still-open action items from the last `generation.prior_actions_lookback_meetings` meetings that share at least one attendee with the current meeting. Each open item (id + description + owner + due date + source meeting title) is injected into the prompt via the `prior_actions` template variable, rendered by the shared `prior_actions_block` macro.

The LLM is instructed to populate `prior_action_updates[]` only for items actually acknowledged in the current meeting — not to echo every prior item. After ingestion completes, `PipelineOrchestrator._apply_prior_action_updates()` reads the freshly written minutes JSON, and for each update calls `StorageEngine.update_action_item_status(action_item_id, new_status)` where `new_status ∈ {done, in_progress, cancelled}`. Invalid / unknown action item ids are skipped silently. The number of applied transitions is logged.

This whole path is gated by `generation.close_acknowledged_actions` (default true) and is best-effort — DB hiccups never block generation.

### 4A.3.2 Fallback rendering (text+regex path and older meetings)

When the structured LLM call fails and the pipeline falls back to text+regex parsing, the result contains `sections: list[MinutesSection]` (with heading/content/type) but no `discussion_points`. The API route always reads the on-disk minutes JSON to extract `sections[]` — this field is never persisted to the DB. The Minutes tab frontend filter-renders `sections[]` as collapsible cards (identical visual style to `discussion_points`) when `discussion_points` is empty, filtering out sections whose headings duplicate existing dedicated cards (Summary, Decisions, Action Items, Key Topics, Risks, Follow-ups, Parking Lot). This keeps every meeting — structured or text+regex, new or legacy — in the same card-based view.

### 4A.4 JSON-Mode Structured Generation (Ollama, OpenAI, OpenRouter)

For non-Anthropic providers, the `LLMClient._generate_structured_via_json()` method provides structured output via JSON-mode:

1. The tool_definition schema is converted to field descriptions and injected into the system prompt
2. The model is instructed to respond with ONLY valid JSON matching the schema
3. The response is stripped of any markdown code fences (`\`\`\`json ... \`\`\``) before parsing
4. If JSON parsing fails, the response falls back to text mode

This enables Ollama, OpenAI, and OpenRouter models to produce structured `StructuredMinutesResponse` output without requiring Anthropic's native tool_use feature.

### 4A.5 Fallback to Text + Regex

When structured output fails (API error, schema validation failure, JSON parse error), the system falls back to the original text-based generation with regex parsing via `MinutesParser`.

---

## 5. Output Formats

### 5.1 Primary Output: Structured Markdown

The markdown output always opens with header metadata (including `Confidentiality:` when classified), then TL;DR, then Summary. Empty sections are omitted entirely.

```markdown
# {title}

**Date**: {date}
**Duration**: {duration}
**Attendees**: {attendee_list}
**Organizer**: {organizer}
**Confidentiality**: {public | internal | confidential | restricted}
**Sentiment**: {positive | neutral | mixed | negative | constructive | tense}

## TL;DR
{~100-word executive digest: biggest decision, biggest risk, most urgent action, single takeaway}

## Summary
{2-6 sentence executive summary — length depends on meeting type}

## Detailed Notes
{narrative walkthrough; length governed by generation.length_mode}

## {Type-Specific Sections}
...

## Action Items
- [ ] [{PRIORITY}] {action} — Owner: {owner} (Due: {date})

## Decisions
- {decision} (by {made_by})
  *Rationale: {rationale}*

## Risks & Concerns
- {risk} (raised by {name})

## Open Questions
- {question} _(raised by {name}; owner: {name})_

## Follow-ups
- {item} — {owner} ({timeframe})

## Parking Lot
- {deferred topic}

## Prior Action Item Updates
- `{action_item_id}` → **{done | in_progress | cancelled}** — _{evidence quote}_

## Follow-up Email Draft
**Subject:** {subject}
**To:** {attendees}
**Cc:** {optional}

{body — short recap, bulleted decisions, bulleted action items, one-line closing}

## Meeting Effectiveness
- Clear agenda: {Yes | No}
- Decisions made: {n}
- Action items assigned: {n}
- Unresolved items: {n}
```

### 5.2 Output JSON (for System 3 ingestion)

```json
{
  "schema_version": "1.0",
  "meeting_id": "uuid-from-system-1",
  "minutes_id": "uuid",
  "generated_at": "2026-03-28T11:00:00Z",
  "meeting_type": "standup",
  "metadata": {
    "title": "Daily Standup",
    "date": "2026-03-28",
    "duration": "00:15:00",
    "attendees": ["Alice", "Bob", "Carol"],
    "organizer": "Alice"
  },
  "tldr": "~100-word executive digest covering the single biggest decision, biggest risk, most urgent action, and takeaway.",
  "summary": "Brief executive summary...",
  "detailed_notes": "Narrative walkthrough of how the meeting unfolded...",
  "confidentiality": "internal",
  "sections": [
    {
      "heading": "Alice",
      "content": "...",
      "type": "person_update"
    }
  ],
  "action_items": [
    {
      "id": "ai-001",
      "description": "Review PR #423",
      "owner": "Bob",
      "due_date": "2026-03-29",
      "status": "open",
      "priority": "high",
      "mentioned_at_seconds": 234,
      "transcript_segment_ids": [12, 13]
    }
  ],
  "decisions": [
    {
      "id": "d-001",
      "description": "Proceed with Option B for the database migration",
      "made_by": "Alice",
      "rationale": "Better long-term scalability and lower operational cost",
      "confidence": "high",
      "mentioned_at_seconds": 567,
      "transcript_segment_ids": [28, 29, 30]
    }
  ],
  "key_topics": ["database migration", "Q2 planning", "hiring"],
  "sentiment": "positive",
  "structured_data": {},
  "participants": [
    { "name": "Alice", "role": "organizer", "contribution_summary": "Led discussion..." }
  ],
  "discussion_points": [
    { "topic": "Database migration", "description": "...", "speaker": "Alice", "outcome": "decided" }
  ],
  "risks_and_concerns": [],
  "open_questions": [
    { "question": "Do we still need the Databricks contract?", "raised_by": "Bob", "owner": "Alice" }
  ],
  "follow_ups": [],
  "parking_lot": [],
  "prior_action_updates": [
    { "action_item_id": "ai-0f2a1b", "new_status": "done", "evidence": "Alice confirmed the doc was shared" }
  ],
  "email_draft": {
    "subject": "Standup recap — DB migration unblocked",
    "to": ["Alice", "Bob", "Carol"],
    "cc": [],
    "body": "Team,\n\n- Decision: proceed with Option B for migration\n- Bob to review PR #423 by 2026-03-29\n\nThanks!"
  },
  "meeting_effectiveness": {
    "had_clear_agenda": true,
    "decisions_made": 1,
    "action_items_assigned": 2,
    "unresolved_items": 1
  },
  "minutes_markdown": "# Meeting Minutes: Daily Standup\n...",
  "llm": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "tokens_used": { "input": 15000, "output": 2000 },
    "cost_usd": 0.057,
    "processing_time_seconds": 12
  }
}
```

### 5.3 Additional Export Formats

| Format | Use Case |
|--------|----------|
| Markdown (`.md`) | Default, version-control friendly |
| PDF | Formal distribution |
| HTML | Email-friendly, web viewing |
| Google Doc | Auto-create in Google Docs via API |
| Confluence page | Auto-publish to Confluence wiki |
| Notion page | Auto-publish to Notion |
| Slack message | Post summary to a Slack channel |
| Email | Manually send minutes to selected recipients |

---

## 6. Quality Assurance

### 6.1 Automated Checks

- **Completeness**: Verify all speakers appear in the minutes
- **Action item extraction**: Cross-check that imperative statements ("Bob will...", "Alice needs to...") are captured
- **Decision detection**: Flag sentences with decision keywords that may have been missed
- **Length check**: Minutes should be 10-30% of original transcript length (configurable)
- **Hallucination guard**: Flag any names, dates, or numbers in minutes that don't appear in transcript
- **Structured data validation**: When using tool_use output, validate that the `StructuredMinutesResponse` Pydantic model parses without errors and all required fields are populated

### 6.2 Confidence Scoring

- Overall confidence score for the generated minutes (0-1)
- Per-section confidence (did the LLM have enough context?)
- Flag low-confidence sections for human review

### 6.3 Human Review Workflow

- Option to open generated minutes in an editor for review before finalizing
- Diff view showing which parts of the transcript each section was derived from
- One-click approve or edit-and-approve
- Track whether minutes have been reviewed (`status: draft | reviewed | approved`)

---

## 7. Customization

### 7.1 Custom Prompt Templates

Custom meeting-type templates are created by convention: drop a new `<type>.md.j2` file into `templates/` and the type becomes usable immediately. No config block is required — `PromptRouter._discover_all_types()` globs the directory on every run, excluding files whose stem starts with `_` (treated as shared macro includes).

A custom template should import the shared macros to get the cross-cutting baseline (TL;DR, omit-empty rule, length guidance, vendor injection, risks, open questions, prior-action carryover, email draft, confidentiality) for free:

```jinja
{# templates/strategy_offsite.md.j2 — a user-defined type #}
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
3-5 sentences: key strategic decisions, top bets, biggest unresolved tension.

## Strategic Bets
For each: bet / timeframe / owner / success criteria / investment.

## Cross-Functional Commitments
Populate `action_items` with a single owner per commitment.

{{ m.vendor_feedback_block(vendors) }}
{{ m.risks_block() }}
{{ m.open_questions_block() }}
{{ m.prior_actions_block(prior_actions) }}

## Key Topics
5-10 short labels.

{{ m.email_draft_block() }}
{{ m.confidentiality_block() }}
```

The LLM classifier auto-discovers the new type: it scans each `.md.j2` file's system prompt and section headings via `_extract_type_descriptions()` to build its enum. Existing heuristic fallback keywords for calendar titles are hardcoded in `PromptRouter.classify_meeting_type()` — if you want the new type detected from titles without relying on the LLM path, add a pattern there.

### 7.2 Post-Processing Hooks

- **Email distribution**: User can manually trigger sending minutes to selected recipients (never automatic)
- **Slack posting**: Post summary to configured Slack channel
- **Calendar update**: Attach minutes to the original calendar event
- **Task creation**: Create tasks in project management tools (Jira, Linear, Asana) from action items
- **CRM update**: Log client call notes to CRM (Salesforce, HubSpot)

### 7.3 Language & Localization

- Generate minutes in a different language than the transcript
- Support for multilingual meetings (transcript in mixed languages, minutes in one language)
- Configurable formality level (casual, professional, formal)

---

## 8. Cost Management

### 8.1 Token Usage Tracking

- Track input/output tokens per generation
- Calculate cost per meeting based on provider pricing
- Monthly usage reports and budget alerts

### 8.2 Cost Optimization

- Use smaller/cheaper models for simple meeting types (standups)
- Use larger models for complex meetings (decision meetings, board meetings)
- Cache prompt templates to reduce token overhead
- Pre-summarize very long transcripts before sending to LLM

```yaml
cost_optimization:
  model_by_type:
    standup: "claude-haiku-4-5-20251001"
    one_on_one_direct_report: "claude-sonnet-4-6"
    one_on_one_leader: "claude-sonnet-4-6"
    customer_meeting: "claude-opus-4-6"
    decision_meeting: "claude-opus-4-6"
    default: "claude-sonnet-4-6"
  monthly_budget_usd: 50.00
  alert_threshold_percent: 80
```

---

## 9. Tech Stack

| Component | Technology |
|-----------|-----------|
| Orchestration | Python with `asyncio` |
| LLM clients | `anthropic` SDK, `openai` SDK (also used for OpenRouter), `ollama` |
| Prompt management | Jinja2 templates + YAML config |
| Markdown processing | `markdown-it-py` |
| PDF export | `weasyprint` or `reportlab` |
| Google Docs export | Google Docs API |
| Email | SMTP or Gmail API |
| Slack integration | Slack SDK (`slack_sdk`) |
| Task tracking | Jira/Linear/Asana APIs |
