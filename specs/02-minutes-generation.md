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

The keyword-matching classifier has been replaced with an LLM call using Claude Haiku for improved accuracy.

**How it works:**
1. Sends the first 4000 characters of the transcript plus metadata (speaker count, calendar title) to Claude Haiku
2. Uses Anthropic `tool_use` with an enum constraint to guarantee a valid meeting type is returned
3. The LLM returns `meeting_type`, `confidence` (0-1), and `reasoning`
4. The classifier reads actual template descriptions (system prompt + section headings) via `_extract_type_descriptions()` to inform its decision
5. Custom template types added to the `templates/` directory are auto-discovered

**Cost:** Approximately $0.001 per classification.

**Fallback:** If the Anthropic API is unavailable, the system falls back to keyword matching.

**Trigger:** Runs automatically when the initial transcript confidence is below 0.7.

**Meeting Type Refinement (N11):** After the initial classification, the system can refine the meeting type based on the full transcript content, improving accuracy for borderline cases.

### 2.2 Supported Meeting Types & Templates

| Meeting Type | Template Focus | Key Sections |
|-------------|---------------|--------------|
| `customer_meeting` | Client requirements, commitments | Client requests, Our commitments, Timeline, Follow-ups |
| `one_on_one_direct_report` | Coaching, growth, performance | Discussion topics, Feedback given, Career development, Action items, Follow-ups |
| `one_on_one_leader` | Updates, alignment, support needs | Status updates, Guidance received, Decisions, Escalations, Action items |
| `standup` | Brief, per-person updates | Yesterday, Today, Blockers per person |
| `team_meeting` | Decisions, financials, strategy | Prior action items review, Decisions (with rationale), Financial review, Blockers (4 categories + cross-team), Strategic updates, Technology decisions, Service feedback, Customer impact, Resource & capacity, Team health, Announcements, Parking lot, Action items (split by urgency) |
| `interview` | Candidate assessment | Questions asked, Candidate responses (summarized), Assessment notes, Recommendation |
| `brainstorm` | Ideas and themes | Ideas generated, Themes/clusters, Top ideas, Next steps |
| `decision_meeting` | Options and outcomes | Context, Options discussed, Pros/cons, Decision made, Rationale |
| `presentation` | Key takeaways | Presenter, Topic, Key points, Q&A summary, Audience feedback |
| `all_hands` | Company updates | Announcements, Department updates, Q&A highlights, Key dates |
| `retrospective` | Improvement actions | What went well, What didn't, Action items for improvement |
| `planning` | Sprint/project plan | Goals, Stories/tasks discussed, Estimates, Commitments, Risks |
| `workshop` | Learning outcomes | Topics covered, Exercises, Key learnings, Resources shared |
| `other` | General-purpose | Summary, Key discussion points, Decisions, Action items |

### 2.3 Template Selection Logic

```
1. Check meeting_type from transcript JSON
2. If confidence >= 0.7 -> use corresponding template
3. If confidence < 0.7 -> run LLM classifier on transcript excerpt
4. If user override provided -> use override
5. If no match -> fall back to "other" (general-purpose) template
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

### 4.2 LLM Configuration

```yaml
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

- `summary` (str): Executive summary of the meeting
- `sentiment` (str): Overall meeting sentiment (positive, neutral, negative, mixed)
- `participants` (list[ParticipantInfo]): Participant details with name, role, contribution summary, and per-speaker sentiment (positive/neutral/negative/mixed)
- `discussion_points` (list[DiscussionPoint]): Key topics with description, speaker, and outcome
- `action_items` (list[StructuredActionItem]): With description, owner, due_date, priority (high/medium/low), transcript_segment_ids
- `decisions` (list[StructuredDecision]): With description, made_by, rationale, confidence (high/medium/low), transcript_segment_ids
- `risks_and_concerns` (list[RiskConcern]): Identified risks with description, severity, owner
- `follow_ups` (list[FollowUp]): Items needing follow-up with description, owner, timeline
- `parking_lot` (list[str]): Topics raised but deferred
- `meeting_effectiveness` (MeetingEffectiveness): Rating (1-5) and notes on meeting quality (A4: meeting effectiveness scoring)
- `key_topics` (list[str]): Extracted topic keywords
- `structured_data` (dict): Meeting-type-specific structured data
- `minutes_markdown` (str): Full rendered markdown of the minutes

### 4A.3 Tool Use Approach

1. The `LLMClient.generate_structured()` method constructs a tool definition from the schema
2. The tool is passed with `tool_choice: {"type": "tool", "name": "structured_minutes"}` to force the LLM to use it
3. The response is parsed directly into a `StructuredMinutesResponse` Pydantic model
4. A `StructuredMinutesAdapter` converts the structured response into the standard `ParsedMinutes` format used by the rest of the pipeline

### 4A.3.1 Persistence of structured data

The `MinutesJSONWriter` in `system2/output.py` populates `MinutesJSON.structured_data` with a dict containing all structured fields (sentiment, participants, discussion_points, risks_and_concerns, follow_ups, parking_lot, key_topics, meeting_effectiveness, decisions, action_items). This field is serialised by `StorageEngine.upsert_meeting()` into the `minutes.structured_json` TEXT column, and deserialised by the API route to populate the `MinutesResponse` seen by the frontend. This ensures the structured view in the Minutes tab works end-to-end (before this fix, structured fields existed on disk but were NULL in the DB, so the UI only showed Summary + Decisions + Actions).

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

```markdown
# Meeting Minutes: {title}

**Date**: {date}
**Duration**: {duration}
**Type**: {meeting_type}
**Attendees**: {attendee_list}
**Organizer**: {organizer}

## Summary
{2-3 sentence executive summary}

## {Type-Specific Sections}
...

## Action Items
- [ ] {action} — **{owner}** {due date if known}

## Decisions Made
- {decision 1}
- {decision 2}

## Next Steps
- {next step}

---
*Generated from transcript {meeting_id} on {generation_date}*
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
  "summary": "Brief executive summary...",
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
  "follow_ups": [],
  "parking_lot": [],
  "meeting_effectiveness": { "rating": 4, "notes": "Focused and productive" },
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

Users can create custom meeting type templates:

```yaml
custom_templates:
  - name: "board_meeting"
    description: "Quarterly board meeting minutes"
    detection_keywords: ["board", "quarterly review", "shareholder"]
    detection_attendee_count: ">= 8"
    template_file: "templates/board_meeting.md"
    output_format: "pdf"
    distribution: ["confluence"]

  - name: "sales_call"
    description: "Sales/prospect call notes"
    detection_keywords: ["pricing", "demo", "proposal", "contract"]
    detection_calendar_label: "Sales"
    template_file: "templates/sales_call.md"
    crm_integration: true
```

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
