# Change Meeting Type & Regenerate Summary

**Status:** Proposed.
**Scope tag:** Single-user, local-first MVP. No multi-user, no cloud, no auto-email.

## 1. Problem

Meeting type is decided at recording time (user pick → falls back to LLM auto-classify when confidence is low). Once minutes are generated, the type is locked in: the only way to change it today is to delete the meeting and re-record, which throws away the audio + transcript + diarization work.

Real failure modes this leaves on the table:
- Auto-classifier picked `team_meeting` when it should have been `decision_meeting` — the resulting summary buries the decisions inside a generic team-update structure.
- User mis-tagged a `one_on_one` as `one_on_one_leader` when it was actually `one_on_one_direct_report` — perspective is wrong, action items are framed for the wrong person.
- A recording the user assumed was a `standup` turned into a 45-minute `architecture_review`. Standup template is too thin to capture what was discussed.

We already have the type-aware template router and the reprocess pipeline. We just don't expose "switch the type and re-render" anywhere in the UI.

## 2. User-facing behavior

In the **Minutes** tab of `MeetingDetail.svelte`, beside the existing meeting-type badge, add an **Edit type** affordance (pencil icon or small "Change" link). Clicking it swaps the badge for a `<select>` populated from the `MeetingType` enum, with two buttons: **Regenerate** and **Cancel**.

Confirmation flow:
1. User picks a new type in the dropdown.
2. User clicks **Regenerate**. A modal asks: *"This will discard the current minutes and rebuild them with the `<new type>` template. The transcript, audio, and external notes are preserved. Continue?"* — single confirm button.
3. On confirm: the meetings type pill flips to a "Regenerating…" pill (reuse the spinner pattern from the External-notes tab, [MeetingDetail.svelte:184-230](web/src/lib/components/MeetingDetail.svelte:184)), and the Minutes tab body grays out.
4. The frontend polls `GET /meetings/{id}` every 4 s (reuse `externalNotesPoller`) watching a new `meeting_type_status` field flip from `processing` → `ready` (or `error`).
5. On `ready`: refetch the meeting detail, render the new minutes, surface a toast: *"Minutes regenerated as <new type>"*. The new badge replaces the dropdown.
6. On `error`: surface the error string, keep the old minutes intact, leave the dropdown open so the user can retry or cancel.

Edge cases the UI must handle:
- **Same type picked.** If the user picks the type that's already set, **Regenerate** stays disabled (cheap guardrail — the existing `/regenerate` endpoint is the right tool for "rerun with the same type").
- **External notes present.** Show a small note in the modal: *"Your pasted external notes will be re-injected into the new summary."* — no extra action needed; the pipeline already does this.
- **No transcript.** Hide the **Edit type** affordance entirely (mirror the external-notes precondition at [meetings.py:907-912](src/meeting_minutes/api/routes/meetings.py:907)). A meeting that never finished transcribing can't be re-summarized.
- **Concurrent regen.** If `meeting_type_status == "processing"` already (from a previous click), disable **Edit type** until it clears.

## 3. Backend

### 3.1 Endpoint

```
POST /meetings/{meeting_id}/meeting-type
Body: { "meeting_type": "decision_meeting" }
→ 202 Accepted
  { "status": "accepted",
    "meeting_id": "...",
    "meeting_type_status": "processing" }
```

`POST` not `PATCH`: the side effect is "regenerate the entire minutes document," not a property tweak. Following the external-notes shape ([meetings.py:860-929](src/meeting_minutes/api/routes/meetings.py:860)) keeps the async-status convention consistent.

Validation (synchronous, before returning 202):
1. Meeting exists → else 404.
2. `meeting_type` parses cleanly into the `MeetingType` enum → else 422 with the list of valid values.
3. Transcript file exists at `data/transcripts/{id}.json` → else 400 (same precondition as external-notes).
4. New type ≠ current type on `MeetingORM` → else 400 (`"Meeting is already type X — use /regenerate to rerun without changing the type"`).
5. No regen already in flight (`meeting_type_status != "processing"`) → else 409.

Sync portion before returning 202:
- Update `data/notes/{id}.json`: set `meeting_type = <new>`, set `meeting_type_status = "processing"`, drop any stale `meeting_type_error`.
- Write atomically via the existing [`write_notes_sidecar`](src/meeting_minutes/external_notes.py:77).

Async portion (fire-and-forget `asyncio.create_task`):
- Call `PipelineOrchestrator.reprocess(meeting_id)` ([pipeline.py:550](src/meeting_minutes/pipeline.py:550) reads `meeting_type` from the sidecar at line 592, so no extra plumbing needed).
- Re-export to Obsidian (reuse `_export_to_obsidian_from_file`).
- On success: `meeting_type_status = "ready"`. On exception: `meeting_type_status = "error"`, `meeting_type_error = str(exc)`.

### 3.2 Reusable scaffolding

Lift the status-tracking helpers out of `external_notes.py` into a small generic helper, since meeting-type now becomes the second consumer of the same pattern:

```
src/meeting_minutes/sidecar_status.py
  set_status(data_dir, meeting_id, key, status, error=None)
```

`key` is `"external_notes_status"` or `"meeting_type_status"`. Migration path: change `external_notes.set_status` to delegate to this helper; keep the public name as a shim. Both consumers now share atomic-write + error-clearing semantics.

New module:

```
src/meeting_minutes/meeting_type_change.py
  async def run_background_retype(config, meeting_id) -> None
  def schedule_background_retype(config, meeting_id) -> asyncio.Task
```

Mirrors the structure of `external_notes.run_background_update` ([external_notes.py:263-360](src/meeting_minutes/external_notes.py:263)), minus the speaker-inference and post-append steps. Roughly 50 lines.

### 3.3 Pipeline changes

**None required.** The pipeline already:
- Reads `meeting_type` from the sidecar ([pipeline.py:592](src/meeting_minutes/pipeline.py:592)).
- Honors a user override and skips auto-classify ([pipeline.py:639-645](src/meeting_minutes/pipeline.py:639)).
- Writes the new type back into the `MeetingORM` row during ingestion.

External-notes preservation also works for free: the post-append step writes `## External notes` after regeneration ([external_notes.py:333-355](src/meeting_minutes/external_notes.py:333)), but a meeting-type-change doesn't run that path. We need to **manually replay** the `## External notes` re-append after retype if the sidecar has `external_notes` set, otherwise the user's paste vanishes from the rendered markdown.

In `run_background_retype`, after `orchestrator.reprocess` returns, check `sidecar.get("external_notes")` — if present, call `append_section_to_local_files` + `update_db_markdown`. Same three lines that already run inside the external-notes background job.

### 3.4 Database

No schema changes. `meetings.meeting_type` (`String`) already exists at [db.py:38-46](src/meeting_minutes/system3/db.py:38). The pipeline's existing ingestion already writes the new type to that column.

The `meeting_type_status` field lives **only** in the sidecar JSON, not in the DB — same as `external_notes_status`. No migration.

### 3.5 GET /meetings/{id} response

Surface the new sidecar fields in the meeting-detail response so the frontend can poll them:

```
{ ...,
  "meeting_type": "decision_meeting",
  "meeting_type_status": "ready" | "processing" | "error" | null,
  "meeting_type_error": "..." | null,
  "external_notes_status": ...
}
```

Read them from the sidecar in `_export_meeting_to_response` (or wherever external-notes-status is currently surfaced). Treat absence as `null`.

## 4. Frontend

### 4.1 Component changes

`web/src/lib/components/MeetingDetail.svelte`:

- New local state: `editingType: boolean`, `pendingType: MeetingType | null`, `meetingTypePoller: number | null`.
- Replace the static `<MeetingTypeBadge>` in the minutes-tab header with a switch:
  - `editingType === false && meetingTypeStatus !== "processing"` → badge + small "Change" button.
  - `editingType === true` → `<select>` + Cancel + Regenerate buttons.
  - `meetingTypeStatus === "processing"` → spinner pill *"Regenerating as `<pendingType>`…"* (no buttons).
  - `meetingTypeStatus === "error"` → red pill with the error + Retry/Dismiss.
- New `MeetingType` enum import (mirror what the recorder UI does — there should already be a constant somewhere; if not, hard-code from the Python enum and add a TS test that asserts they match).
- New `confirmRegenerate()` modal — reuse the existing dialog component if one exists, otherwise inline `<dialog>`.
- New `pollMeetingType()` — copy `pollExternalNotes` ([MeetingDetail.svelte:184-230](web/src/lib/components/MeetingDetail.svelte:184)) and adjust field names. **Refactor opportunity:** generalize both pollers into a single `pollSidecarStatus(field)` helper — small enough to do in this PR.
- On `ready`: `await refreshMeeting()`, toast, reset state.
- On `error`: surface error, leave dropdown open with `pendingType` preserved.

### 4.2 Type ordering in the dropdown

The 17 enum values are too flat for a one-shot select. Group them in `<optgroup>`s for scanability:

- **1:1s** — one_on_one_direct_report, one_on_one_leader, one_on_one_peer, one_on_one *(legacy)*
- **Team rituals** — standup, team_meeting, retrospective, planning, brainstorm
- **Decision & review** — decision_meeting, architecture_review, incident_review, leadership_meeting, board_meeting, interview_debrief
- **External** — customer_meeting, vendor_meeting
- **Other** — other

The current type is selected by default. Group ordering source: live in the frontend constant alongside the type list — no backend change.

## 5. Testing

Unit (Python):
- `meeting_type_change.run_background_retype` — happy path: sidecar updated, reprocess called, status flips to ready.
- Same with `external_notes` present in sidecar — verify the `## External notes` section is re-appended after reprocess.
- Failure path: `reprocess` raises → status = error, error string captured.
- Endpoint: 422 on bad enum value, 400 on missing transcript, 400 on same-type, 409 on already-processing, 202 on success.

Integration:
- Record fixture meeting (existing test fixtures) → assert minutes generated as `team_meeting` template.
- Call new endpoint with `decision_meeting` → poll until ready → assert markdown contains decision-meeting-template-specific sections (e.g. "Decisions Made" header) and does **not** contain team-meeting-only sections.
- Same flow with external notes pre-pasted → assert `## External notes` section survives in the regenerated `.md` and DB column.

Frontend:
- Visual regression on the new edit-mode switcher (badge → select → spinner → badge).
- Polling behavior under `error` response.

## 6. Out of scope

- **Versioning / history of regenerations.** Old summary is overwritten — same model as `/regenerate` today. If we want to keep a history, that's a separate spec.
- **Bulk re-type.** No "select 10 meetings and re-run as X" UI. Single-meeting only.
- **Custom prompt overrides per regen.** The existing `instructions` sidecar field already covers ad-hoc prompt steering — this feature only changes the *template*, not the prompt text.
- **Auto-detect type drift.** No "we noticed this meeting type might be wrong" suggestion. The user drives it.

## 7. Migration / rollout

No data migration. Feature is purely additive: existing meetings work unchanged, new endpoint is opt-in via the new UI control. If we ever revert the feature, the only artifact left behind is the `meeting_type_status` key in some sidecars — harmless and ignored by all readers.
